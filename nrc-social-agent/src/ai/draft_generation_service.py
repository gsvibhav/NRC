"""Top-level orchestration for the Primary Draft Generation Engine
(Milestone 6).

Wires together — without redefining any of them — the completed content
plan (src/ai/content_plan_manager.py, read-only here except for one
bookkeeping flip, see below), the completed analysis
(src/ai/analysis_manager.py, read-only), the persisted clarification
context (read directly off the workflow document), the Claude adapter
(client.py), the output-specific generation prompt/schema/parser/validator,
and the existing WorkflowManager / ConversationManager. Handlers never
construct a Claude prompt or touch the workflow/draft documents directly;
they call `generate_draft()` (right after content planning completes, or to
`/retry` a stalled attempt) and act only on the returned
`DraftGenerationOutcome`.

Milestone 5 owns the strategy; this module must never make another
planning decision. Concretely: it always writes exactly the plan's single
priority-1 output (content_plan_validation.py already guarantees exactly
one exists on a COMPLETED plan — the `NoPrimaryOutputError` check here is
defense in depth, not a decision point), it never asks Claude to
reconsider the output type or platform (see draft_prompts.py's shared
system-prompt rules), and it never generates a second output or a second
variant of the same output.

Idempotency is checked before any state-eligibility gate, unlike
content_planning_service.py: a completed content plan never changes the
workflow's state, so that service's state check is always valid across
retries. Draft generation *does* transition the workflow (GENERATING_CONTENT
-> SHOWING_PREVIEW) on success, so a workflow already sitting in
SHOWING_PREVIEW with a matching READY_FOR_REVIEW draft is just as valid an
idempotent replay as one still in GENERATING_CONTENT with no draft yet —
checking the draft store first (via DraftManager.find_existing) lets both
cases short-circuit identically, with no second Claude call either way.

Retryable vs. permanent failures mirror content_planning_service.py: a
ClaudeRetryableError leaves state/pointer untouched and sets
`metadata["pending_retry"] = "primary_draft_generation"` (lowercase
snake_case, matching this codebase's existing pending-retry values --
"analysis", "clarification_decision", "content_planning" -- rather than the
brief's own suggested `PRIMARY_DRAFT_GENERATION`, a casing this file
deliberately does not follow, to keep the four values internally
consistent); every other AnalysisError moves the workflow to FAILED and
clears the pointer via src/conversation/lifecycle.py.

Persist-before-transition-before-preview ordering: this service persists
the draft record and attaches the workflow's lightweight reference *in the
same atomic write that also transitions the workflow to SHOWING_PREVIEW*
(WorkflowManager.attach_generated_draft_reference) before returning to the
caller. Sending the Telegram preview is handlers.py's job, strictly after
this method returns successfully -- never before.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from .content_plan_models import OUTPUT_TYPE_REGISTRY, ContentPlanStatus, OutputType
from .draft_manager import DraftManager
from .draft_models import DraftSourceVersions, DraftStatus, DraftUsage
from .draft_parser import parse_draft_response
from .draft_prompts import (
    DRAFT_GENERATION_SYSTEM_PROMPTS,
    DRAFT_PROMPT_VERSION,
    DRAFT_RESPONSE_SCHEMAS,
    build_draft_generation_user_prompt,
)
from .draft_validation import VALIDATORS_BY_OUTPUT_TYPE
from .errors import (
    AnalysisError,
    ClaudeRetryableError,
    DraftValidationFailedError,
    MissingAnalysisForPlanningError,
    MissingContentPlanForGenerationError,
    NoPrimaryOutputError,
    UnsupportedGenerationOutputTypeError,
    WorkflowNotEligibleForDraftGenerationError,
)

logger = logging.getLogger(__name__)

_RETRY_PRIMARY_DRAFT_GENERATION = "primary_draft_generation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DraftGenerationOutcome:
    workflow_id: str
    draft_id: str
    output_id: str
    output_type: str
    content: dict


class DraftGenerationService:
    def __init__(
        self,
        *,
        model: str,
        schema_version: int,
        max_instagram_caption_length: int,
        max_hashtags: int,
        claude_client: ClaudeClient,
        analysis_manager: AnalysisManager,
        content_plan_manager: ContentPlanManager,
        draft_manager: DraftManager,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._model = model
        self._schema_version = schema_version
        self._max_instagram_caption_length = max_instagram_caption_length
        self._max_hashtags = max_hashtags
        self._claude_client = claude_client
        self._analysis_manager = analysis_manager
        self._content_plan_manager = content_plan_manager
        self._draft_manager = draft_manager
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager

    async def generate_draft(self, *, workflow_id: str, telegram_user_id: int) -> DraftGenerationOutcome:
        """Generate (or reuse) the primary draft for `workflow_id`. Raises
        an AnalysisError subclass on any expected failure. Never returns a
        result unless a draft actually reached READY_FOR_REVIEW (freshly,
        or reused from an existing one)."""

        logger.info(
            "Draft generation requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id
        )

        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise AnalysisError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        plan = self._content_plan_manager.find_existing(workflow_id)
        if plan is None or plan.status is not ContentPlanStatus.COMPLETED:
            raise MissingContentPlanForGenerationError(f"workflow_id={workflow_id} has no completed content plan")

        primary_output = self._select_primary_output(plan.outputs, workflow_id=workflow_id)

        existing_draft = self._draft_manager.find_existing(workflow_id, primary_output.output_id)
        if existing_draft is not None and existing_draft.status is DraftStatus.READY_FOR_REVIEW:
            logger.info(
                "Duplicate draft generation skipped workflow_id=%s output_id=%s (already READY_FOR_REVIEW)",
                workflow_id, primary_output.output_id,
            )
            return DraftGenerationOutcome(
                workflow_id=workflow_id,
                draft_id=existing_draft.draft_id,
                output_id=existing_draft.output_id,
                output_type=existing_draft.output_type,
                content=existing_draft.content or {},
            )

        if workflow.state is not WorkflowState.GENERATING_CONTENT:
            raise WorkflowNotEligibleForDraftGenerationError(
                f"workflow_id={workflow_id} is in state={workflow.state.value}, not GENERATING_CONTENT"
            )

        try:
            output_type_enum = OutputType(primary_output.output_type)
        except ValueError as exc:
            raise UnsupportedGenerationOutputTypeError(
                f"output_type={primary_output.output_type!r} is not a recognized registry entry"
            ) from exc
        registry_entry = OUTPUT_TYPE_REGISTRY.get(output_type_enum)
        if registry_entry is None or not registry_entry.supports_generation:
            raise UnsupportedGenerationOutputTypeError(
                f"output_type={primary_output.output_type!r} does not support generation yet"
            )

        analysis = self._analysis_manager.find_existing(workflow_id)
        if analysis is None or analysis.result is None:
            raise MissingAnalysisForPlanningError(f"workflow_id={workflow_id} has no completed analysis")

        clarification_context = (
            ClarificationContext.from_dict(workflow.clarification_context)
            if workflow.clarification_context
            else None
        )

        user_prompt = build_draft_generation_user_prompt(
            analysis=analysis.result,
            clarification_context=clarification_context,
            conversation_turns=workflow.conversation,
            strategy=plan.strategy,
            output=primary_output,
        )

        logger.info(
            "Draft generation context loaded workflow_id=%s output_id=%s output_type=%s",
            workflow_id, primary_output.output_id, primary_output.output_type,
        )

        try:
            model, content, usage = self._call_and_validate(
                user_prompt, output_type=primary_output.output_type, workflow_id=workflow_id
            )
        except ClaudeRetryableError as exc:
            logger.warning("Draft generation retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, existing_draft, plan=plan, output=primary_output, failure_reason=type(exc).__name__)
            self._set_pending_retry(workflow_id)
            raise
        except AnalysisError as exc:
            logger.error("Draft generation permanent failure workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, existing_draft, plan=plan, output=primary_output, failure_reason=type(exc).__name__)
            self._mark_permanent_failure(workflow_id, telegram_user_id)
            raise

        source_versions = DraftSourceVersions(
            analysis_schema_version=analysis.schema_version,
            content_plan_schema_version=plan.schema_version,
            draft_prompt_version=DRAFT_PROMPT_VERSION,
            clarification_context_version=clarification_context.version if clarification_context else None,
        )

        document = self._record_success(
            workflow_id, existing_draft, plan=plan, output=primary_output,
            model=model, content=content, source_versions=source_versions, usage=usage,
        )
        self._attach_reference(workflow_id, document)
        self._mark_plan_output_generated(workflow_id, primary_output.output_id)
        self._clear_pending_retry(workflow_id)

        logger.info(
            "Draft persisted workflow_id=%s draft_id=%s output_id=%s output_type=%s",
            workflow_id, document.draft_id, document.output_id, document.output_type,
        )

        return DraftGenerationOutcome(
            workflow_id=workflow_id,
            draft_id=document.draft_id,
            output_id=document.output_id,
            output_type=document.output_type,
            content=document.content or {},
        )

    def _select_primary_output(self, outputs: list, *, workflow_id: str):
        """content_plan_validation.py already guarantees exactly one
        priority-1 output on any plan that reached COMPLETED — this is a
        defensive re-check, not a place where this service picks among
        candidates. Never selects any output other than priority 1."""

        primary_candidates = [o for o in outputs if o.priority == 1]
        if len(primary_candidates) != 1:
            raise NoPrimaryOutputError(
                f"workflow_id={workflow_id} plan has {len(primary_candidates)} priority-1 output(s), expected 1"
            )
        return primary_candidates[0]

    def _call_and_validate(self, user_prompt: str, *, output_type: str, workflow_id: str):
        """Calls Claude, parses, and validates -- with exactly one
        controlled regeneration attempt if validation fails (never an
        unbounded loop), mirroring content_planning_service.py's identical
        pattern. Returns (model, content_dict, DraftUsage).

        Validates against `user_prompt` itself as the grounding corpus, for
        the same reason content_planning_service.py does: it already
        contains the full analysis, clarification context, conversation
        text, strategy, and selected output -- a separately-assembled
        corpus would only duplicate it."""

        system_prompt = DRAFT_GENERATION_SYSTEM_PROMPTS[output_type]
        response_schema = DRAFT_RESPONSE_SCHEMAS[output_type]
        validator = VALIDATORS_BY_OUTPUT_TYPE[output_type]

        logger.info("Claude draft generation request started workflow_id=%s output_type=%s", workflow_id, output_type)
        response = self._claude_client.generate_draft(
            system_prompt=system_prompt, user_prompt=user_prompt, response_schema=response_schema,
        )
        logger.info(
            "Claude draft generation request completed workflow_id=%s duration_seconds=%.2f "
            "input_tokens=%s output_tokens=%s",
            workflow_id, response.duration_seconds, response.input_tokens, response.output_tokens,
        )
        content = parse_draft_response(output_type, response.text)

        try:
            validator(
                content,
                max_caption_length=self._max_instagram_caption_length,
                max_hashtags=self._max_hashtags,
                grounding_text=user_prompt,
            )
            logger.info("Draft validation succeeded workflow_id=%s", workflow_id)
        except DraftValidationFailedError as exc:
            logger.warning("Draft validation failed workflow_id=%s reason=%s", workflow_id, exc)
            retry_prompt = (
                user_prompt
                + f"\n\nYour previous draft was rejected by validation ({exc}). "
                "Produce a corrected draft that avoids that problem, for the exact same selected output."
            )
            retry_response = self._claude_client.generate_draft(
                system_prompt=system_prompt, user_prompt=retry_prompt, response_schema=response_schema,
            )
            content = parse_draft_response(output_type, retry_response.text)
            validator(
                content,
                max_caption_length=self._max_instagram_caption_length,
                max_hashtags=self._max_hashtags,
                grounding_text=retry_prompt,
            )
            logger.info("Draft validation succeeded after one regeneration workflow_id=%s", workflow_id)
            response = retry_response

        usage = DraftUsage(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        return response.model, content.to_dict(), usage

    def _record_success(self, workflow_id, existing_draft, *, plan, output, model, content, source_versions, usage):
        kwargs = dict(
            workflow_id=workflow_id, plan_id=plan.plan_id, output_id=output.output_id,
            output_type=output.output_type, model=model, schema_version=self._schema_version,
            prompt_version=DRAFT_PROMPT_VERSION, status=DraftStatus.READY_FOR_REVIEW,
            content=content, source_versions=source_versions.to_dict(), usage=usage.to_dict(),
        )
        if existing_draft is not None:
            kwargs.pop("plan_id")
            kwargs.pop("output_type")
            return self._draft_manager.supersede_failed_draft(**kwargs)
        return self._draft_manager.create_draft(**kwargs)

    def _record_failure(self, workflow_id, existing_draft, *, plan, output, failure_reason: str) -> None:
        kwargs = dict(
            workflow_id=workflow_id, plan_id=plan.plan_id, output_id=output.output_id,
            output_type=output.output_type, model=self._model,
            schema_version=self._schema_version, prompt_version=DRAFT_PROMPT_VERSION,
            status=DraftStatus.FAILED, metadata={"failure_reason": failure_reason},
        )
        try:
            if existing_draft is not None:
                kwargs.pop("plan_id")
                kwargs.pop("output_type")
                self._draft_manager.supersede_failed_draft(**kwargs)
            else:
                self._draft_manager.create_draft(**kwargs)
        except AnalysisError:
            logger.error("Failed to persist draft failure record workflow_id=%s", workflow_id, exc_info=True)

    def _attach_reference(self, workflow_id: str, document) -> None:
        reference = {
            "draft_id": document.draft_id,
            "output_id": document.output_id,
            "output_type": document.output_type,
            "status": document.status.value,
            "schema_version": document.schema_version,
            "completed_at": document.updated_at,
        }
        try:
            self._workflow_manager.attach_generated_draft_reference(
                workflow_id, reference, new_state=WorkflowState.SHOWING_PREVIEW
            )
        except WorkflowError:
            logger.error("Failed to attach generated-draft reference workflow_id=%s", workflow_id, exc_info=True)

    def _mark_plan_output_generated(self, workflow_id: str, output_id: str) -> None:
        try:
            self._content_plan_manager.mark_output_generated(workflow_id=workflow_id, output_id=output_id)
        except AnalysisError:
            logger.error(
                "Failed to mark plan output generated workflow_id=%s output_id=%s",
                workflow_id, output_id, exc_info=True,
            )

    def _set_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": _RETRY_PRIMARY_DRAFT_GENERATION})
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
