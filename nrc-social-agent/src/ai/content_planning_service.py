"""Top-level orchestration for the Content Planning Engine (Milestone 5).

Wires together — without redefining any of them — the persisted analysis
(src/ai/analysis_manager.py, read-only here), the persisted clarification
context (read directly off the workflow document), the Claude adapter
(client.py), the planning prompt/parser/validator, and the existing
WorkflowManager / ConversationManager. Handlers never construct a Claude
prompt or touch the workflow document directly; they call `plan_content()`
(right after clarification reaches GENERATING_CONTENT, or to /retry a
stalled attempt) and act only on the returned `ContentPlanOutcome`.

Eligibility is checked before anything else, with **no side effects** on
failure (no FAILED transition, no plan record) — `WorkflowNotEligibleForPlanningError`
and `MissingAnalysisForPlanningError` both signal "this method shouldn't
have been called yet," not a planning failure. Callers (handlers.py) are
expected to only invoke this once clarification has actually reached
GENERATING_CONTENT.

Strategy vs. writing: this service never asks Claude to write final copy,
and content_plan_validation.py rejects a response that reads like it
anyway (see that module for the specific heuristics and their documented
limitations). One bounded regeneration attempt is made on a validation
failure — mirroring clarification_service.py's question-regeneration
pattern — before it becomes a permanent failure.

Retryable vs. permanent failures mirror analysis_service.py and
clarification_service.py: a ClaudeRetryableError leaves state/pointer
untouched and sets `metadata["pending_retry"] = "content_planning"`; every
other AnalysisError moves the workflow to FAILED and clears the pointer
via src/conversation/lifecycle.py.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.states import WorkflowState
from .analysis_manager import AnalysisManager
from .clarification_models import ClarificationContext
from .client import ClaudeClient
from .content_plan_manager import ContentPlanManager
from .content_plan_models import ContentPlanStatus, ContentPlanUsage
from .content_plan_parser import parse_content_plan_response
from .content_plan_prompts import (
    CONTENT_PLANNING_POLICY_VERSION,
    CONTENT_PLANNING_SYSTEM_PROMPT,
    CONTENT_PLAN_RESPONSE_SCHEMA,
    build_content_planning_user_prompt,
)
from .content_plan_validation import validate_content_plan
from .errors import (
    AnalysisError,
    ClaudeRetryableError,
    ContentPlanValidationFailedError,
    MissingAnalysisForPlanningError,
    WorkflowNotEligibleForPlanningError,
)

logger = logging.getLogger(__name__)

_RETRY_CONTENT_PLANNING = "content_planning"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ContentPlanOutcome:
    workflow_id: str
    plan_id: str
    outputs: list  # PlannedOutput, sorted by priority ascending


class ContentPlanningService:
    def __init__(
        self,
        *,
        model: str,
        schema_version: int,
        max_outputs: int,
        claude_client: ClaudeClient,
        analysis_manager: AnalysisManager,
        content_plan_manager: ContentPlanManager,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._model = model
        self._schema_version = schema_version
        self._max_outputs = max_outputs
        self._claude_client = claude_client
        self._analysis_manager = analysis_manager
        self._content_plan_manager = content_plan_manager
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager

    async def plan_content(self, *, workflow_id: str, telegram_user_id: int) -> ContentPlanOutcome:
        """Run (or reuse) content planning for `workflow_id`. Raises an
        AnalysisError subclass on any expected failure. Never returns a
        result unless a plan actually completed successfully (freshly, or
        reused from an existing COMPLETED plan)."""

        logger.info("Content planning requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id)

        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise AnalysisError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        if workflow.state is not WorkflowState.GENERATING_CONTENT:
            raise WorkflowNotEligibleForPlanningError(
                f"workflow_id={workflow_id} is in state={workflow.state.value}, not GENERATING_CONTENT"
            )

        existing = self._content_plan_manager.find_existing(workflow_id)
        if existing is not None and existing.status is ContentPlanStatus.COMPLETED:
            logger.info("Duplicate content planning skipped workflow_id=%s (already COMPLETED)", workflow_id)
            return ContentPlanOutcome(
                workflow_id=workflow_id,
                plan_id=existing.plan_id,
                outputs=sorted(existing.outputs, key=lambda o: o.priority),
            )

        analysis = self._analysis_manager.find_existing(workflow_id)
        if analysis is None or analysis.result is None:
            raise MissingAnalysisForPlanningError(f"workflow_id={workflow_id} has no completed analysis")

        clarification_context = (
            ClarificationContext.from_dict(workflow.clarification_context)
            if workflow.clarification_context
            else None
        )

        user_prompt = build_content_planning_user_prompt(
            analysis=analysis.result,
            clarification_context=clarification_context,
            conversation_turns=workflow.conversation,
            max_outputs=self._max_outputs,
        )

        logger.info("Planning context loaded workflow_id=%s", workflow_id)

        try:
            response = self._call_and_validate(user_prompt, workflow_id=workflow_id)
        except ClaudeRetryableError as exc:
            logger.warning("Content planning retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, existing, failure_reason=type(exc).__name__)
            self._set_pending_retry(workflow_id)
            raise
        except AnalysisError as exc:
            logger.error("Content planning permanent failure workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, existing, failure_reason=type(exc).__name__)
            self._mark_permanent_failure(workflow_id, telegram_user_id)
            raise

        model, parsed, usage = response
        outputs = [replace(o, output_id=uuid.uuid4().hex) for o in parsed.outputs]
        outputs.sort(key=lambda o: o.priority)

        document = self._record_success(
            workflow_id, existing, model=model, strategy=parsed.strategy, outputs=outputs,
            excluded_outputs=parsed.excluded_outputs, usage=usage,
        )
        self._attach_reference(workflow_id, document)
        self._clear_pending_retry(workflow_id)
        logger.info(
            "Content plan persisted workflow_id=%s plan_id=%s output_count=%d primary_output_type=%s",
            workflow_id, document.plan_id, len(outputs),
            outputs[0].output_type if outputs else None,
        )

        return ContentPlanOutcome(workflow_id=workflow_id, plan_id=document.plan_id, outputs=outputs)

    def _call_and_validate(self, user_prompt: str, *, workflow_id: str):
        """Calls Claude, parses, and validates — with exactly one
        controlled regeneration attempt if validation fails (never an
        unbounded loop). Returns (model, ParsedContentPlan, ContentPlanUsage).

        Validates against `user_prompt` itself as the grounding corpus —
        it already contains the full analysis, clarification context, and
        conversation text (see build_content_planning_user_prompt), so a
        separately-assembled corpus would only duplicate it."""

        logger.info("Claude planning request started workflow_id=%s model=%s", workflow_id, self._model)
        response = self._claude_client.plan_content(
            system_prompt=CONTENT_PLANNING_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_schema=CONTENT_PLAN_RESPONSE_SCHEMA,
        )
        logger.info(
            "Claude planning request completed workflow_id=%s duration_seconds=%.2f input_tokens=%s output_tokens=%s",
            workflow_id, response.duration_seconds, response.input_tokens, response.output_tokens,
        )
        parsed = parse_content_plan_response(response.text)

        try:
            validate_content_plan(parsed, max_outputs=self._max_outputs, grounding_text=user_prompt)
            logger.info("Plan validation succeeded workflow_id=%s", workflow_id)
        except ContentPlanValidationFailedError as exc:
            logger.warning("Plan validation failed workflow_id=%s reason=%s", workflow_id, exc)
            retry_prompt = (
                user_prompt
                + f"\n\nYour previous plan was rejected by validation ({exc}). "
                "Produce a corrected plan that avoids that problem."
            )
            retry_response = self._claude_client.plan_content(
                system_prompt=CONTENT_PLANNING_SYSTEM_PROMPT,
                user_prompt=retry_prompt,
                response_schema=CONTENT_PLAN_RESPONSE_SCHEMA,
            )
            parsed = parse_content_plan_response(retry_response.text)
            validate_content_plan(parsed, max_outputs=self._max_outputs, grounding_text=retry_prompt)
            logger.info("Plan validation succeeded after one regeneration workflow_id=%s", workflow_id)
            response = retry_response

        usage = ContentPlanUsage(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        return response.model, parsed, usage

    def _record_success(self, workflow_id, existing, *, model, strategy, outputs, excluded_outputs, usage):
        kwargs = dict(
            workflow_id=workflow_id, model=model, schema_version=self._schema_version,
            prompt_version=CONTENT_PLANNING_POLICY_VERSION, status=ContentPlanStatus.COMPLETED,
            strategy=strategy, outputs=outputs, excluded_outputs=excluded_outputs, usage=usage,
        )
        if existing is not None:
            return self._content_plan_manager.supersede_failed_content_plan(**kwargs)
        return self._content_plan_manager.create_content_plan(**kwargs)

    def _record_failure(self, workflow_id, existing, *, failure_reason: str) -> None:
        kwargs = dict(
            workflow_id=workflow_id, model=self._model, schema_version=self._schema_version,
            prompt_version=CONTENT_PLANNING_POLICY_VERSION, status=ContentPlanStatus.FAILED,
            metadata={"failure_reason": failure_reason},
        )
        try:
            if existing is not None:
                self._content_plan_manager.supersede_failed_content_plan(**kwargs)
            else:
                self._content_plan_manager.create_content_plan(**kwargs)
        except AnalysisError:
            logger.error("Failed to persist content-plan failure record workflow_id=%s", workflow_id, exc_info=True)

    def _attach_reference(self, workflow_id: str, document) -> None:
        reference = {
            "plan_id": document.plan_id,
            "status": document.status.value,
            "schema_version": document.schema_version,
            "output_count": len(document.outputs),
            "primary_output_type": document.outputs[0].output_type if document.outputs else None,
            "completed_at": document.updated_at,
        }
        try:
            self._workflow_manager.attach_content_plan_reference(workflow_id, reference)
        except WorkflowError:
            logger.error("Failed to attach content-plan reference workflow_id=%s", workflow_id, exc_info=True)

    def _set_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": _RETRY_CONTENT_PLANNING})
        except WorkflowError:
            logger.error("Failed to set pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _clear_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to clear pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _mark_permanent_failure(self, workflow_id: str, telegram_user_id: int) -> None:
        try:
            self._workflow_manager.update_state(workflow_id, WorkflowState.FAILED)
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to mark workflow FAILED workflow_id=%s", workflow_id, exc_info=True)
            return

        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id,
            workflow_state=WorkflowState.FAILED,
            conversation_manager=self._conversation_manager,
        )
