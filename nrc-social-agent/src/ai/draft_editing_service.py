"""Top-level orchestration for the Draft Editing Service (Milestone 7).

By the time `edit_draft()` runs, the workflow is already in
`GENERATING_CONTENT` with a `pending_edit` record whose instruction has
already been persisted as a conversation turn (see
WorkflowManager.record_edit_instruction() and handlers.py's EDITING-state
text handling) — this service never receives raw Telegram text directly,
and never decides whether an instruction should be accepted; that
eligibility gate already happened one layer up.

Failure taxonomy — a deliberate, documented refinement of
docs/WORKFLOW.md §3.6's blanket "generation fails -> FAILED" rule,
because unlike a first-time generation, editing always has a perfectly
good *existing* current draft to fall back to:

- **Retryable** (`ClaudeRetryableError`, or a `DraftVersionConflictError`
  racing the current pointer): leaves the current draft, `pending_edit`,
  and workflow state completely untouched, and sets
  `metadata["pending_retry"] = "draft_editing"` so `/retry` can safely
  resume using the *same already-persisted instruction* — never asks the
  user to retype it.
- **Non-corrupting permanent** (an out-of-scope instruction, a malformed/
  mismatched Claude response, a validation failure surviving one bounded
  corrective attempt, or a detected stale parent version): the current
  draft is left completely unchanged, no new version is created, and the
  workflow returns to `SHOWING_PREVIEW` — never `FAILED`.
- **Corrupting permanent** (missing content plan, no current draft to
  edit, auth/configuration errors, or a workflow found in a data-
  inconsistent state that should be unreachable in normal operation): the
  workflow moves to `FAILED` and the active pointer is cleared, exactly
  like every other pipeline stage's permanent-failure handling.

Idempotency: before ever calling Claude, this checks `pending_edit`'s own
status. If it's already `"COMPLETED"` (a duplicate Telegram update, or a
`/retry` arriving after the operation actually already finished), the
already-persisted resulting version is returned directly — no second
Claude call, no second version.
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
from .content_plan_models import ContentPlanStatus
from .draft_edit_prompts import (
    DRAFT_EDIT_RESPONSE_SCHEMAS,
    DRAFT_EDIT_SYSTEM_PROMPTS,
    EDIT_PROMPT_VERSION,
    build_draft_edit_user_prompt,
)
from .draft_edit_validation import detect_unsupported_platform_switch, validate_draft_edit
from .draft_manager import DraftManager
from .draft_models import DraftUsage
from .draft_parser import parse_draft_response
from .draft_version_manager import DraftVersionManager, resolve_current_draft
from .errors import (
    AnalysisError,
    ClaudeRetryableError,
    DraftEditValidationFailedError,
    DraftResponseSchemaMismatchError,
    DraftValidationFailedError,
    DraftVersionConflictError,
    MalformedDraftResponseError,
    MissingAnalysisForPlanningError,
    MissingContentPlanForGenerationError,
    NoPendingEditInstructionError,
    NoPrimaryOutputError,
    StaleDraftVersionError,
    UnsupportedEditRequestError,
    WorkflowNotEligibleForEditError,
)

logger = logging.getLogger(__name__)

_RETRY_DRAFT_EDITING = "draft_editing"

# Failures where the current draft stays perfectly valid — the workflow
# returns to SHOWING_PREVIEW rather than FAILED. See module docstring.
# Includes DraftValidationFailedError (draft_validation.py's shared
# content-quality rules, reused unchanged by validate_draft_edit() for the
# base checks) alongside DraftEditValidationFailedError (the edit-specific
# instruction-alignment checks) — either can fire from the same
# validate_draft_edit() call, and both mean the same thing here: the
# *revision* was rejected, not the workflow.
_NON_CORRUPTING_ERRORS = (
    UnsupportedEditRequestError,
    MalformedDraftResponseError,
    DraftResponseSchemaMismatchError,
    DraftValidationFailedError,
    DraftEditValidationFailedError,
    StaleDraftVersionError,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DraftEditOutcome:
    workflow_id: str
    draft_id: str
    output_id: str
    output_type: str
    version_number: int
    content: dict


class DraftEditingService:
    def __init__(
        self,
        *,
        schema_version: int,
        max_instagram_caption_length: int,
        max_hashtags: int,
        claude_client: ClaudeClient,
        analysis_manager: AnalysisManager,
        content_plan_manager: ContentPlanManager,
        draft_manager: DraftManager,
        draft_version_manager: DraftVersionManager,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._schema_version = schema_version
        self._max_instagram_caption_length = max_instagram_caption_length
        self._max_hashtags = max_hashtags
        self._claude_client = claude_client
        self._analysis_manager = analysis_manager
        self._content_plan_manager = content_plan_manager
        self._draft_manager = draft_manager
        self._draft_version_manager = draft_version_manager
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager

    async def edit_draft(self, *, workflow_id: str, telegram_user_id: int) -> DraftEditOutcome:
        """Process the currently-persisted edit instruction for
        `workflow_id`. Raises an AnalysisError subclass on any expected
        failure. Never returns a result unless a revision actually reached
        READY_FOR_REVIEW (freshly created, or reused from an
        already-completed operation)."""

        logger.info("Draft edit requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id)

        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise AnalysisError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        pending_edit = workflow.pending_edit
        if (
            workflow.state is not WorkflowState.GENERATING_CONTENT
            or pending_edit is None
            or pending_edit.get("status") not in ("INSTRUCTION_RECEIVED", "COMPLETED")
        ):
            raise WorkflowNotEligibleForEditError(
                f"workflow_id={workflow_id} is not mid-edit (state={workflow.state.value}, "
                f"pending_edit={pending_edit!r})"
            )

        output_id = pending_edit["output_id"]

        current_document, current_pointer, pointer_etag = resolve_current_draft(
            draft_version_manager=self._draft_version_manager,
            draft_manager=self._draft_manager,
            workflow_id=workflow_id,
            output_id=output_id,
        )

        if pending_edit.get("status") == "COMPLETED":
            completed_version_number = pending_edit.get("completed_version_number")
            if completed_version_number is not None and current_pointer.current_version_number == completed_version_number:
                logger.info(
                    "Duplicate edit request skipped workflow_id=%s output_id=%s (already COMPLETED at version=%s)",
                    workflow_id, output_id, completed_version_number,
                )
                return self._make_outcome(workflow_id, output_id, current_document)
            # A "COMPLETED" pending_edit whose recorded version no longer
            # matches the authoritative current pointer is a data
            # inconsistency, not a normal duplicate — treated as corrupting.
            raise NoPendingEditInstructionError(
                f"workflow_id={workflow_id} pending_edit marked COMPLETED but does not match current pointer"
            )

        expected_parent_version = pending_edit["expected_parent_version"]
        if current_pointer.current_version_number != expected_parent_version:
            # The pointer has already moved past what this operation
            # expected. Before treating this as a stale/foreign conflict,
            # check whether the CURRENT version is actually *this
            # operation's own* result — i.e. create_next_version()
            # already succeeded on an earlier attempt, but a crash
            # happened before complete_edit() could catch up the workflow
            # document (which is the only place `pending_edit` and
            # `generated_draft` actually live). If so, this isn't a
            # conflict at all: finish the bookkeeping and return the
            # already-persisted result, rather than incorrectly bouncing
            # a successful edit back to SHOWING_PREVIEW pointed at a
            # stale version number.
            operation_id = pending_edit.get("operation_id")
            edit_ref = current_document.edit_instruction_reference or {}
            if (
                current_document.parent_version_number == expected_parent_version
                and operation_id is not None
                and edit_ref.get("instruction_id") == operation_id
            ):
                logger.info(
                    "Edit already completed by an earlier attempt, catching up workflow bookkeeping "
                    "workflow_id=%s output_id=%s version=%s",
                    workflow_id, output_id, current_pointer.current_version_number,
                )
                return self._catch_up_completed_edit(
                    workflow_id, output_id, current_document, current_pointer, pending_edit
                )

            # Otherwise, a genuinely stale/foreign version — should be
            # unreachable in normal single-user operation, since nothing
            # else can advance the pointer while this workflow sits in
            # GENERATING_CONTENT mid-edit. Still returns cleanly to
            # SHOWING_PREVIEW rather than leaving the workflow stuck here
            # with no recovery path.
            self._workflow_manager.fail_edit_to_preview(workflow_id)
            raise StaleDraftVersionError(
                f"workflow_id={workflow_id} expected to edit version={expected_parent_version} "
                f"but current is version={current_pointer.current_version_number}"
            )

        instruction = _find_conversation_turn_content(workflow.conversation, pending_edit.get("instruction_turn_id"))
        if instruction is None:
            raise NoPendingEditInstructionError(
                f"workflow_id={workflow_id} has no persisted instruction turn for pending_edit"
            )

        switched_channel = detect_unsupported_platform_switch(instruction)
        if switched_channel is not None:
            logger.info(
                "Edit rejected: out-of-scope platform switch workflow_id=%s requested_channel=%s",
                workflow_id, switched_channel,
            )
            self._workflow_manager.fail_edit_to_preview(workflow_id)
            raise UnsupportedEditRequestError(f"instruction requested switching to channel={switched_channel!r}")

        plan = self._content_plan_manager.find_existing(workflow_id)
        if plan is None or plan.status is not ContentPlanStatus.COMPLETED:
            raise MissingContentPlanForGenerationError(f"workflow_id={workflow_id} has no completed content plan")

        target_output = next((o for o in plan.outputs if o.output_id == output_id), None)
        if target_output is None:
            raise NoPrimaryOutputError(
                f"workflow_id={workflow_id} plan has no output_id={output_id} matching the draft under review"
            )

        analysis = self._analysis_manager.find_existing(workflow_id)
        if analysis is None or analysis.result is None:
            raise MissingAnalysisForPlanningError(f"workflow_id={workflow_id} has no completed analysis")

        clarification_context = (
            ClarificationContext.from_dict(workflow.clarification_context)
            if workflow.clarification_context
            else None
        )

        user_prompt = build_draft_edit_user_prompt(
            current_content=current_document.content or {},
            instruction=instruction,
            strategy=plan.strategy,
            output=target_output,
            analysis=analysis.result,
            clarification_context=clarification_context,
            current_version_number=current_pointer.current_version_number,
        )

        logger.info(
            "Draft edit context loaded workflow_id=%s output_id=%s parent_version=%s",
            workflow_id, output_id, current_pointer.current_version_number,
        )

        try:
            model, content, usage = self._call_and_validate(
                user_prompt,
                output_type=target_output.output_type,
                parent_content=current_document.content or {},
                instruction=instruction,
                workflow_id=workflow_id,
            )
        except ClaudeRetryableError as exc:
            logger.warning("Draft edit retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._workflow_manager.set_edit_pending_retry(workflow_id)
            raise
        except DraftVersionConflictError as exc:
            logger.warning("Draft edit lost a version-pointer race workflow_id=%s error=%s", workflow_id, exc)
            self._workflow_manager.set_edit_pending_retry(workflow_id)
            raise
        except _NON_CORRUPTING_ERRORS as exc:
            logger.warning("Draft edit rejected (current draft unaffected) workflow_id=%s error=%s", workflow_id, exc)
            self._workflow_manager.fail_edit_to_preview(workflow_id)
            raise
        except AnalysisError as exc:
            logger.error("Draft edit permanent failure workflow_id=%s error=%s", workflow_id, exc)
            self._mark_permanent_failure(workflow_id, telegram_user_id)
            raise

        edit_instruction_reference = {"instruction_id": pending_edit["operation_id"], "created_at": _now_iso()}

        try:
            new_version = self._draft_version_manager.create_next_version(
                workflow_id=workflow_id,
                output_id=output_id,
                plan_id=plan.plan_id,
                output_type=target_output.output_type,
                parent_version_number=current_pointer.current_version_number,
                expected_pointer_etag=pointer_etag,
                content=content,
                model=model,
                schema_version=self._schema_version,
                prompt_version=EDIT_PROMPT_VERSION,
                source_versions=current_document.source_versions,
                usage=usage.to_dict(),
                edit_instruction_reference=edit_instruction_reference,
            )
        except DraftVersionConflictError as exc:
            logger.warning(
                "Draft edit lost the version-creation race workflow_id=%s error=%s", workflow_id, exc
            )
            self._workflow_manager.set_edit_pending_retry(workflow_id)
            raise

        generated_draft_reference = {
            "draft_id": new_version.draft_id,
            "output_id": output_id,
            "output_type": new_version.output_type,
            "current_version": new_version.version_number,
            "status": "READY_FOR_REVIEW",
            "schema_version": new_version.schema_version,
            "updated_at": new_version.updated_at,
        }
        completed_pending_edit = {
            **pending_edit,
            "status": "COMPLETED",
            "completed_version_number": new_version.version_number,
        }
        self._workflow_manager.complete_edit(
            workflow_id, generated_draft_reference=generated_draft_reference, pending_edit=completed_pending_edit
        )

        logger.info(
            "Draft edit persisted workflow_id=%s output_id=%s new_version=%s",
            workflow_id, output_id, new_version.version_number,
        )

        return DraftEditOutcome(
            workflow_id=workflow_id,
            draft_id=new_version.draft_id,
            output_id=output_id,
            output_type=new_version.output_type,
            version_number=new_version.version_number,
            content=new_version.content or {},
        )

    def _call_and_validate(self, user_prompt: str, *, output_type: str, parent_content: dict, instruction: str, workflow_id: str):
        """Calls Claude, parses, and validates — with exactly one
        controlled corrective attempt if validation fails (never an
        unbounded loop), mirroring draft_generation_service.py's identical
        pattern. Returns (model, content_dict, DraftUsage)."""

        system_prompt = DRAFT_EDIT_SYSTEM_PROMPTS[output_type]
        response_schema = DRAFT_EDIT_RESPONSE_SCHEMAS[output_type]

        logger.info("Claude edit request started workflow_id=%s output_type=%s", workflow_id, output_type)
        response = self._claude_client.edit_draft(
            system_prompt=system_prompt, user_prompt=user_prompt, response_schema=response_schema,
        )
        logger.info(
            "Claude edit request completed workflow_id=%s duration_seconds=%.2f input_tokens=%s output_tokens=%s",
            workflow_id, response.duration_seconds, response.input_tokens, response.output_tokens,
        )
        content = parse_draft_response(output_type, response.text)

        try:
            validate_draft_edit(
                content,
                parent_content=parent_content,
                instruction=instruction,
                max_caption_length=self._max_instagram_caption_length,
                max_hashtags=self._max_hashtags,
                grounding_text=user_prompt,
            )
            logger.info("Draft edit validation succeeded workflow_id=%s", workflow_id)
        except (DraftValidationFailedError, DraftEditValidationFailedError) as exc:
            logger.warning("Draft edit validation failed workflow_id=%s reason=%s", workflow_id, exc)
            retry_prompt = (
                user_prompt
                + f"\n\nYour previous revision was rejected by validation ({exc}). "
                "Produce a corrected revision that avoids that problem, following the same instruction."
            )
            retry_response = self._claude_client.edit_draft(
                system_prompt=system_prompt, user_prompt=retry_prompt, response_schema=response_schema,
            )
            content = parse_draft_response(output_type, retry_response.text)
            validate_draft_edit(
                content,
                parent_content=parent_content,
                instruction=instruction,
                max_caption_length=self._max_instagram_caption_length,
                max_hashtags=self._max_hashtags,
                grounding_text=retry_prompt,
            )
            logger.info("Draft edit validation succeeded after one correction workflow_id=%s", workflow_id)
            response = retry_response

        usage = DraftUsage(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        return response.model, content.to_dict(), usage

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

    def _catch_up_completed_edit(self, workflow_id, output_id, current_document, current_pointer, pending_edit):
        """The immutable version and current pointer already reflect this
        exact operation's successful result (create_next_version()
        succeeded on an earlier attempt), but the workflow document's own
        `generated_draft`/`pending_edit` never got updated to match —
        e.g. a crash between that write and complete_edit(). Finishes the
        bookkeeping and returns the already-persisted result; never calls
        Claude or creates another version."""

        generated_draft_reference = {
            "draft_id": current_document.draft_id,
            "output_id": output_id,
            "output_type": current_document.output_type,
            "current_version": current_pointer.current_version_number,
            "status": "READY_FOR_REVIEW",
            "schema_version": current_document.schema_version,
            "updated_at": current_document.updated_at,
        }
        completed_pending_edit = {
            **pending_edit,
            "status": "COMPLETED",
            "completed_version_number": current_pointer.current_version_number,
        }
        self._workflow_manager.complete_edit(
            workflow_id, generated_draft_reference=generated_draft_reference, pending_edit=completed_pending_edit
        )
        return self._make_outcome(workflow_id, output_id, current_document)

    @staticmethod
    def _make_outcome(workflow_id: str, output_id: str, document) -> DraftEditOutcome:
        return DraftEditOutcome(
            workflow_id=workflow_id,
            draft_id=document.draft_id,
            output_id=output_id,
            output_type=document.output_type,
            version_number=document.version_number,
            content=document.content or {},
        )


def _find_conversation_turn_content(conversation: list, turn_id: str | None) -> str | None:
    if turn_id is None:
        return None
    for turn in conversation:
        if turn.get("turn_id") == turn_id:
            return turn.get("content")
    return None
