"""Top-level orchestration for the adaptive, multi-turn clarification
conversation (Milestone 4B).

Wires together — without redefining any of them — the persisted analysis
(src/ai/analysis_manager.py, read-only here), the Claude adapter
(client.py), the clarification decision prompt/parser/validator, and the
existing WorkflowManager / ConversationManager. Handlers never construct a
Claude prompt or touch the workflow document directly; they call
`evaluate_and_advance()` (right after analysis, or to /retry a stalled
cycle) and `handle_user_reply()` (a plain-text reply while
WAITING_FOR_USER) and act only on the returned `ClarificationOutcome`.

One decision cycle, one Claude call: `_run_decision_cycle()` is the single
place a clarification decision is requested and applied. It is
deliberately reentrant/restart-safe — it derives everything it needs
(structured context, recent conversation, the latest user answer if any)
from the workflow document as currently persisted, rather than from
arguments threaded through from an earlier step. This is what makes
`evaluate_and_advance()` simultaneously correct for: the very first cycle
right after analysis, every cycle after handle_user_reply() persists an
answer, and any `/retry` of a previously failed cycle (see handlers.py) —
there is exactly one code path, not three.

Persist-before-send: `_run_decision_cycle()` always calls
`WorkflowManager.apply_clarification_decision()` (one atomic S3 write —
updated context, the new question turn if any, `pending_question`, and
the state transition, all together) and returns to the caller *before*
any Telegram send happens — handlers.py sends the message only after this
method returns successfully. A crash between "decided to ask" and "sent
the question" therefore always resolves, on the next interaction, to a
workflow that already has the question persisted and can simply be
re-sent or answered.

Retryable vs. permanent failures mirror analysis_service.py: a
ClaudeRetryableError leaves state/pointer untouched and sets
`metadata["pending_retry"] = "clarification_decision"`; every other
AnalysisError moves the workflow to FAILED and clears the pointer via
src/conversation/lifecycle.py.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.models import WorkflowDocument
from ..workflow.states import WorkflowState
from .analysis_manager import AnalysisManager
from .clarification_models import ClarificationContext, ClarificationDecisionType, ContextSource
from .clarification_parser import parse_clarification_response
from .clarification_prompts import (
    CLARIFICATION_POLICY_VERSION,
    CLARIFICATION_RESPONSE_SCHEMA,
    CLARIFICATION_SYSTEM_PROMPT,
    build_clarification_user_prompt,
)
from .client import ClaudeClient
from .errors import (
    AnalysisError,
    ClarificationContextInsufficientError,
    ClaudeRetryableError,
    InvalidClarificationQuestionError,
    NoPendingQuestionError,
    WorkflowNotWaitingForReplyError,
)
from .question_validation import validate_question_text

logger = logging.getLogger(__name__)

_RETRY_CLARIFICATION = "clarification_decision"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ClarificationOutcome:
    workflow_id: str
    decision: ClarificationDecisionType
    question_text: str | None


class ClarificationService:
    def __init__(
        self,
        *,
        max_questions: int,
        claude_client: ClaudeClient,
        analysis_manager: AnalysisManager,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._max_questions = max_questions
        self._claude_client = claude_client
        self._analysis_manager = analysis_manager
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager

    async def evaluate_and_advance(self, *, workflow_id: str, telegram_user_id: int) -> ClarificationOutcome:
        """Run one clarification decision cycle for `workflow_id`'s
        current persisted state. Used for the very first cycle (right
        after analysis, called from handlers.py's media()), and for
        /retry of any previously failed cycle — see the module docstring
        for why this single method is correct for both."""

        workflow = self._load_workflow(workflow_id)
        return await self._run_decision_cycle(workflow, telegram_user_id)

    async def handle_user_reply(
        self,
        *,
        workflow_id: str,
        telegram_user_id: int,
        reply_text: str,
        telegram_update_id: int,
    ) -> ClarificationOutcome | None:
        """Process a plain-text reply while WAITING_FOR_USER. Returns None
        if the reply is a detected duplicate of an already-recorded
        answer (nothing further to do — see _is_duplicate_reply).

        Raises WorkflowNotWaitingForReplyError / NoPendingQuestionError if
        called against a workflow that isn't in the right state — callers
        (handlers.py) are expected to check state before calling this, so
        reaching either of these indicates a caller bug, not ordinary
        user input.
        """

        workflow = self._load_workflow(workflow_id)

        if workflow.state is not WorkflowState.WAITING_FOR_USER:
            raise WorkflowNotWaitingForReplyError(f"workflow_id={workflow_id} is not WAITING_FOR_USER")
        if workflow.pending_question is None:
            raise NoPendingQuestionError(f"workflow_id={workflow_id} has no pending question")

        if self._is_duplicate_reply(workflow, telegram_update_id):
            logger.info(
                "Duplicate reply skipped workflow_id=%s telegram_update_id=%s", workflow_id, telegram_update_id
            )
            return None

        answer_turn = {
            "turn_id": uuid.uuid4().hex,
            "role": "user",
            "type": "clarification_answer",
            "content": reply_text,
            "in_reply_to_question_id": workflow.pending_question["question_id"],
            "created_at": _now_iso(),
            "telegram_update_id": telegram_update_id,
        }
        self._workflow_manager.record_user_reply(workflow_id, turn=answer_turn)
        logger.info("User answer persisted workflow_id=%s", workflow_id)

        return await self.evaluate_and_advance(workflow_id=workflow_id, telegram_user_id=telegram_user_id)

    # --- internal ------------------------------------------------------

    def _load_workflow(self, workflow_id: str) -> WorkflowDocument:
        try:
            return self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise AnalysisError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

    def _is_duplicate_reply(self, workflow: WorkflowDocument, telegram_update_id: int) -> bool:
        return any(
            turn.get("role") == "user" and turn.get("telegram_update_id") == telegram_update_id
            for turn in workflow.conversation
        )

    async def _run_decision_cycle(self, workflow: WorkflowDocument, telegram_user_id: int) -> ClarificationOutcome:
        workflow_id = workflow.workflow_id

        context = (
            ClarificationContext.from_dict(workflow.clarification_context)
            if workflow.clarification_context
            else ClarificationContext()
        )
        analysis = self._analysis_manager.find_existing(workflow_id)
        analysis_summary = analysis.result.summary if analysis and analysis.result else None

        question_count = sum(1 for t in workflow.conversation if t.get("type") == "clarification_question")
        at_limit = question_count >= self._max_questions

        latest_answer = None
        if workflow.conversation and workflow.conversation[-1].get("role") == "user":
            latest_answer = workflow.conversation[-1].get("content")

        user_prompt = build_clarification_user_prompt(
            analysis_summary=analysis_summary,
            context=context,
            conversation_turns=workflow.conversation,
            pending_question=workflow.pending_question,
            question_count=question_count,
            max_questions=self._max_questions,
        )

        logger.info(
            "Clarification evaluation started workflow_id=%s question_count=%d has_latest_answer=%s",
            workflow_id,
            question_count,
            latest_answer is not None,
        )

        try:
            response = self._claude_client.decide_clarification(
                system_prompt=CLARIFICATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=CLARIFICATION_RESPONSE_SCHEMA,
            )
            decision = parse_clarification_response(response.text)
        except ClaudeRetryableError as exc:
            logger.warning("Clarification evaluation retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._set_pending_retry(workflow_id)
            raise
        except AnalysisError as exc:
            logger.error("Clarification evaluation permanent failure workflow_id=%s error=%s", workflow_id, exc)
            self._mark_permanent_failure(workflow_id, telegram_user_id, reason=type(exc).__name__)
            raise

        logger.info(
            "Clarification evaluation completed workflow_id=%s decision=%s context_sufficient=%s",
            workflow_id,
            decision.decision.value,
            decision.context_sufficient,
        )

        source = ContextSource.USER if latest_answer is not None else ContextSource.INFERENCE
        updated_context = context.apply_updates(
            decision.context_updates, unresolved=decision.remaining_uncertainties, updated_at=_now_iso(), source=source
        )

        # Hard, server-side enforcement of the question cap — never trust
        # the model alone to respect it, even though the prompt also
        # instructs it to.
        forced_continue = at_limit and decision.decision is ClarificationDecisionType.ASK_QUESTION
        if forced_continue:
            logger.info("Question limit reached, forcing CONTINUE workflow_id=%s question_count=%d", workflow_id, question_count)

        if decision.decision is ClarificationDecisionType.ASK_QUESTION and not forced_continue:
            return self._persist_question(
                workflow,
                telegram_user_id,
                decision,
                context,
                source=source,
                previous_turns=workflow.conversation,
            )

        if forced_continue and not decision.context_sufficient:
            return self._fail_insufficient_context(workflow_id, telegram_user_id, updated_context)

        return self._persist_continue(workflow_id, updated_context)

    def _persist_question(
        self,
        workflow: WorkflowDocument,
        telegram_user_id: int,
        decision,
        context: ClarificationContext,
        *,
        source: ContextSource,
        previous_turns: list,
    ) -> ClarificationOutcome:
        """`context` is the *pre*-this-cycle context (not yet merged with
        `decision.context_updates`) — merged here, once, after settling on
        the final decision (the original one, or a regenerated one if the
        first proposed question failed validation), so a regenerated
        question's own interpretation of the user's answer is never
        silently dropped in favor of the discarded first attempt's."""

        workflow_id = workflow.workflow_id
        question_text = decision.question.text
        previous_question_texts = [t["content"] for t in previous_turns if t.get("type") == "clarification_question"]

        try:
            validate_question_text(question_text, previous_questions=previous_question_texts)
        except InvalidClarificationQuestionError as exc:
            logger.warning("Generated question failed validation workflow_id=%s reason=%s", workflow_id, exc)
            decision, question_text = self._regenerate_question(
                workflow, telegram_user_id, decision, reason=str(exc), previous_question_texts=previous_question_texts
            )

        updated_context = context.apply_updates(
            decision.context_updates, unresolved=decision.remaining_uncertainties, updated_at=_now_iso(), source=source
        )

        question_id = uuid.uuid4().hex
        question_turn = {
            "turn_id": uuid.uuid4().hex,
            "role": "assistant",
            "type": "clarification_question",
            "content": question_text,
            "question_id": question_id,
            "target_field": decision.question.target_field,
            "created_at": _now_iso(),
        }
        pending_question = {
            "question_id": question_id,
            "text": question_text,
            "purpose": decision.question.purpose,
            "target_field": decision.question.target_field,
            "policy_version": CLARIFICATION_POLICY_VERSION,
        }

        self._workflow_manager.apply_clarification_decision(
            workflow_id,
            clarification_context=updated_context.to_dict(),
            new_state=WorkflowState.WAITING_FOR_USER,
            question_turn=question_turn,
            pending_question=pending_question,
            metadata_updates={"pending_retry": None},
        )
        logger.info("Clarification question persisted workflow_id=%s question_id=%s", workflow_id, question_id)
        return ClarificationOutcome(
            workflow_id=workflow_id, decision=ClarificationDecisionType.ASK_QUESTION, question_text=question_text
        )

    def _regenerate_question(
        self, workflow: WorkflowDocument, telegram_user_id: int, decision, *, reason: str, previous_question_texts: list
    ):
        """One controlled regeneration attempt (see question_validation.py's
        module docstring: "avoid unlimited corrective Claude calls"). If
        the regenerated question also fails validation, or Claude no
        longer wants to ask anything, this fails safely: the workflow is
        marked permanently FAILED rather than sending a broken or
        low-quality question."""

        workflow_id = workflow.workflow_id
        context = (
            ClarificationContext.from_dict(workflow.clarification_context)
            if workflow.clarification_context
            else ClarificationContext()
        )
        analysis = self._analysis_manager.find_existing(workflow_id)
        analysis_summary = analysis.result.summary if analysis and analysis.result else None
        question_count = sum(1 for t in workflow.conversation if t.get("type") == "clarification_question")

        retry_prompt = (
            build_clarification_user_prompt(
                analysis_summary=analysis_summary,
                context=context,
                conversation_turns=workflow.conversation,
                pending_question=workflow.pending_question,
                question_count=question_count,
                max_questions=self._max_questions,
            )
            + f"\n\nYour previous proposed question was rejected by validation ({reason}). "
            "Generate a different question that avoids that problem, or return CONTINUE if none is truly needed."
        )

        try:
            response = self._claude_client.decide_clarification(
                system_prompt=CLARIFICATION_SYSTEM_PROMPT,
                user_prompt=retry_prompt,
                response_schema=CLARIFICATION_RESPONSE_SCHEMA,
            )
            retry_decision = parse_clarification_response(response.text)
            if retry_decision.decision is not ClarificationDecisionType.ASK_QUESTION:
                raise InvalidClarificationQuestionError("regeneration switched away from asking a question")
            validate_question_text(retry_decision.question.text, previous_questions=previous_question_texts)
        except (InvalidClarificationQuestionError, AnalysisError) as exc:
            logger.error("Question regeneration failed workflow_id=%s error=%s", workflow_id, exc)
            self._mark_permanent_failure(workflow_id, telegram_user_id, reason="question_validation_failed")
            raise InvalidClarificationQuestionError("failed to produce a valid question after one retry") from exc

        return retry_decision, retry_decision.question.text

    def _persist_continue(self, workflow_id: str, updated_context: ClarificationContext) -> ClarificationOutcome:
        self._workflow_manager.apply_clarification_decision(
            workflow_id,
            clarification_context=updated_context.to_dict(),
            new_state=WorkflowState.GENERATING_CONTENT,
            question_turn=None,
            pending_question=None,
            metadata_updates={"pending_retry": None},
        )
        logger.info("Clarification sufficient, workflow advanced workflow_id=%s state=GENERATING_CONTENT", workflow_id)
        return ClarificationOutcome(workflow_id=workflow_id, decision=ClarificationDecisionType.CONTINUE, question_text=None)

    def _fail_insufficient_context(
        self, workflow_id: str, telegram_user_id: int, updated_context: ClarificationContext
    ) -> ClarificationOutcome:
        logger.warning("Clarification limit reached with insufficient context workflow_id=%s", workflow_id)
        try:
            self._workflow_manager.apply_clarification_decision(
                workflow_id,
                clarification_context=updated_context.to_dict(),
                new_state=WorkflowState.FAILED,
                question_turn=None,
                pending_question=None,
                metadata_updates={"pending_retry": None, "failure_reason": "clarification_limit_reached_unsafe"},
            )
        except WorkflowError:
            logger.error("Failed to mark workflow FAILED workflow_id=%s", workflow_id, exc_info=True)
            raise ClarificationContextInsufficientError(
                f"workflow_id={workflow_id} hit the clarification limit without sufficient context"
            )

        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id,
            workflow_state=WorkflowState.FAILED,
            conversation_manager=self._conversation_manager,
        )
        raise ClarificationContextInsufficientError(
            f"workflow_id={workflow_id} hit the clarification limit without sufficient context"
        )

    def _set_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": _RETRY_CLARIFICATION})
        except WorkflowError:
            logger.error("Failed to set pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _mark_permanent_failure(self, workflow_id: str, telegram_user_id: int, *, reason: str) -> None:
        try:
            self._workflow_manager.update_state(workflow_id, WorkflowState.FAILED)
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None, "failure_reason": reason})
        except WorkflowError:
            logger.error("Failed to mark workflow FAILED workflow_id=%s", workflow_id, exc_info=True)
            return

        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id,
            workflow_state=WorkflowState.FAILED,
            conversation_manager=self._conversation_manager,
        )
