"""Business logic for workflow lifecycle: create, load, and change state.

This is the only layer application code should call — it never exposes raw
JSON or S3 details (those stay inside repository.py/state_store.py), and it
owns timestamp management and optimistic-concurrency validation so callers
never have to think about either.

Concurrency strategy (see also DECISIONS.md if this is later promoted to an
approved decision): every write is a single, atomic S3 PutObject of the
*entire* document — S3 never exposes a partially-written object to a
reader, so a torn write can never replace a valid document with a corrupt
one. On top of that, updates use optimistic concurrency control: creating a
workflow uses a conditional create (fails if the ID already exists, which
should be practically impossible given UUID4 IDs); updating one carries the
ETag from the last load and fails the write if the object changed since
(surfaced as WorkflowConcurrentModificationError). There is no locking and
no retry-on-conflict in this milestone — a conflict is reported to the
caller rather than silently resolved, since there's no conversation logic
yet that would know how to merge two concurrent changes.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone

from .errors import InvalidWorkflowStateError
from .models import WorkflowDocument
from .repository import WorkflowRepository
from .states import WorkflowState

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowManager:
    def __init__(self, repository: WorkflowRepository) -> None:
        self._repository = repository

    def create_workflow(
        self,
        *,
        workflow_id: str,
        telegram_user_id: int,
        media: dict,
        initial_state: WorkflowState = WorkflowState.ANALYZING_MEDIA,
    ) -> WorkflowDocument:
        """Create and persist a new workflow record under `workflow_id`.

        `workflow_id` is supplied by the caller (see src/media/ingestion.py)
        rather than generated here, because the frozen S3 media layout
        embeds it in the media object's own key — it has to exist before
        the upload happens, not after the workflow record is created.

        Defaults to `ANALYZING_MEDIA` because, per docs/WORKFLOW.md,
        `UPLOADING_MEDIA`'s success exit is `ANALYZING_MEDIA` — by the time
        a workflow record is created (immediately after a successful S3
        media upload), that transition has already happened. No analysis
        behavior runs behind it in this milestone; the record simply waits
        here for a future milestone to act on it.
        """

        if not isinstance(initial_state, WorkflowState):
            raise InvalidWorkflowStateError(f"not a recognized WorkflowState: {initial_state!r}")

        now = _now_iso()
        document = WorkflowDocument(
            workflow_id=workflow_id,
            telegram_user_id=telegram_user_id,
            created_at=now,
            updated_at=now,
            state=initial_state,
            media=media,
        )

        self._repository.save(document, expected_etag=None)
        logger.info(
            "Workflow created workflow_id=%s telegram_user_id=%s state=%s",
            workflow_id,
            telegram_user_id,
            document.state.value,
        )
        return document

    def load_workflow(self, workflow_id: str) -> WorkflowDocument:
        loaded = self._repository.load(workflow_id)
        logger.info("Workflow loaded workflow_id=%s state=%s", workflow_id, loaded.document.state.value)
        return loaded.document

    def update_state(self, workflow_id: str, new_state: WorkflowState) -> WorkflowDocument:
        """Load the current record, move it to `new_state`, and persist the
        change — failing with WorkflowConcurrentModificationError if the
        record changed since it was last loaded (see module docstring)."""

        if not isinstance(new_state, WorkflowState):
            raise InvalidWorkflowStateError(f"not a recognized WorkflowState: {new_state!r}")

        loaded = self._repository.load(workflow_id)
        previous_state = loaded.document.state
        updated_document = replace(loaded.document, state=new_state, updated_at=_now_iso())

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info(
            "Workflow state changed workflow_id=%s from=%s to=%s",
            workflow_id,
            previous_state.value,
            new_state.value,
        )
        return updated_document

    def attach_analysis_reference(self, workflow_id: str, analysis_reference: dict) -> WorkflowDocument:
        """Attach a lightweight analysis reference to the workflow document
        (see src/ai/analysis_service.py) — never the full analysis result,
        which stays solely in the analysis/ document (single source of
        truth, no duplication). Same load-then-conditional-save pattern as
        update_state(); does not change `state`."""

        loaded = self._repository.load(workflow_id)
        updated_document = replace(loaded.document, analysis=analysis_reference, updated_at=_now_iso())

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Workflow analysis reference attached workflow_id=%s", workflow_id)
        return updated_document

    def record_user_reply(self, workflow_id: str, *, turn: dict) -> WorkflowDocument:
        """Append a user's clarification-answer turn (see
        src/ai/clarification_service.py), clear the now-answered
        `pending_question`, and re-enter `ANALYZING_MEDIA` — per
        docs/WORKFLOW.md §3.4 ("Re-entry - from WAITING_FOR_USER once the
        user answers a follow-up question"). One atomic write; handlers
        and AI services never append to `conversation` or touch
        `pending_question` directly."""

        loaded = self._repository.load(workflow_id)
        updated_conversation = [*loaded.document.conversation, turn]
        updated_document = replace(
            loaded.document,
            conversation=updated_conversation,
            pending_question=None,
            state=WorkflowState.ANALYZING_MEDIA,
            updated_at=_now_iso(),
        )

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("User reply recorded workflow_id=%s", workflow_id)
        return updated_document

    def apply_clarification_decision(
        self,
        workflow_id: str,
        *,
        clarification_context: dict,
        new_state: WorkflowState,
        question_turn: dict | None = None,
        pending_question: dict | None = None,
        metadata_updates: dict | None = None,
    ) -> WorkflowDocument:
        """Persist the outcome of one clarification decision cycle (see
        src/ai/clarification_service.py) as a single atomic write: the
        updated structured context, an optional new assistant question
        turn, the new `pending_question` (or None to clear it), the state
        transition (`WAITING_FOR_USER`, `GENERATING_CONTENT`, or `FAILED`
        for the clarification-limit-exhausted case), and any metadata
        updates (e.g. the `pending_retry` marker) — all-or-nothing, so a
        crash between "asked a question" and "marked it pending" can never
        happen. This is the "persist conversation turn and pending-question
        data" step that MUST complete before the question is ever sent to
        Telegram (see clarification_service.py's module docstring)."""

        if not isinstance(new_state, WorkflowState):
            raise InvalidWorkflowStateError(f"not a recognized WorkflowState: {new_state!r}")

        loaded = self._repository.load(workflow_id)
        updated_conversation = loaded.document.conversation
        if question_turn is not None:
            updated_conversation = [*updated_conversation, question_turn]

        updated_metadata = dict(loaded.document.metadata)
        for key, value in (metadata_updates or {}).items():
            if value is None:
                updated_metadata.pop(key, None)
            else:
                updated_metadata[key] = value

        updated_document = replace(
            loaded.document,
            conversation=updated_conversation,
            clarification_context=clarification_context,
            pending_question=pending_question,
            metadata=updated_metadata,
            state=new_state,
            updated_at=_now_iso(),
        )

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info(
            "Clarification decision applied workflow_id=%s new_state=%s question_persisted=%s",
            workflow_id,
            new_state.value,
            question_turn is not None,
        )
        return updated_document

    def attach_generated_draft_reference(
        self, workflow_id: str, generated_draft_reference: dict, *, new_state: WorkflowState
    ) -> WorkflowDocument:
        """Attach a lightweight generated-draft reference to the workflow
        document (see src/ai/draft_generation_service.py) — never the full
        draft content, which stays solely in the drafts/ document (single
        source of truth, no duplication) — and transition to `new_state`
        (`SHOWING_PREVIEW` on success) in the same atomic write, so a
        reader can never observe a workflow with a persisted draft
        reference that hasn't yet moved to the review state, or vice
        versa. Milestone 6's brief requires persistence to complete before
        the state transition and before any Telegram send; combining both
        into one write is what makes that ordering crash-safe rather than
        merely sequential."""

        if not isinstance(new_state, WorkflowState):
            raise InvalidWorkflowStateError(f"not a recognized WorkflowState: {new_state!r}")

        loaded = self._repository.load(workflow_id)
        updated_document = replace(
            loaded.document, generated_draft=generated_draft_reference, state=new_state, updated_at=_now_iso()
        )

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info(
            "Workflow generated-draft reference attached workflow_id=%s new_state=%s", workflow_id, new_state.value
        )
        return updated_document

    def attach_content_plan_reference(self, workflow_id: str, content_plan_reference: dict) -> WorkflowDocument:
        """Attach a lightweight content-plan reference to the workflow
        document (see src/ai/content_planning_service.py) — never the full
        plan, which stays solely in the plans/ document (single source of
        truth, no duplication). Same load-then-conditional-save pattern as
        attach_analysis_reference(); does not change `state` — per
        Milestone 5's brief, a completed plan leaves the workflow in
        GENERATING_CONTENT (an existing state; no new one was invented)."""

        loaded = self._repository.load(workflow_id)
        updated_document = replace(loaded.document, content_plan=content_plan_reference, updated_at=_now_iso())

        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Workflow content-plan reference attached workflow_id=%s", workflow_id)
        return updated_document

    def update_metadata(self, workflow_id: str, updates: dict) -> WorkflowDocument:
        """Shallow-merge `updates` into the workflow's `metadata` dict. A
        value of `None` removes that key (used to clear the `pending_retry`
        marker on a successful retry — see src/ai/analysis_service.py and
        src/ai/clarification_service.py)."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        for key, value in updates.items():
            if value is None:
                updated_metadata.pop(key, None)
            else:
                updated_metadata[key] = value

        updated_document = replace(loaded.document, metadata=updated_metadata, updated_at=_now_iso())
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Workflow metadata updated workflow_id=%s keys=%s", workflow_id, sorted(updates.keys()))
        return updated_document

    # --- Milestone 7: Telegram Draft Review, Editing and Approval Workflow ---

    def enter_editing(self, workflow_id: str, *, pending_edit: dict) -> WorkflowDocument:
        """SHOWING_PREVIEW -> EDITING, persisting the initial `pending_edit`
        record (status "AWAITING_INSTRUCTION") in the same atomic write —
        see src/ai/draft_editing_service.py and handlers.py's review_action()
        for what this record contains and how it's used."""

        loaded = self._repository.load(workflow_id)
        updated_document = replace(
            loaded.document, state=WorkflowState.EDITING, pending_edit=pending_edit, updated_at=_now_iso()
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Workflow entered EDITING workflow_id=%s", workflow_id)
        return updated_document

    def record_edit_instruction(self, workflow_id: str, *, turn: dict, pending_edit: dict) -> WorkflowDocument:
        """Append the user's edit-instruction turn, update `pending_edit`
        (status "INSTRUCTION_RECEIVED"), and transition EDITING ->
        GENERATING_CONTENT — all in one atomic write, so the instruction is
        always durably recorded *before* any Claude call is made (required
        for /retry to safely resume after a crash — see
        draft_editing_service.py)."""

        loaded = self._repository.load(workflow_id)
        updated_conversation = [*loaded.document.conversation, turn]
        updated_document = replace(
            loaded.document,
            conversation=updated_conversation,
            pending_edit=pending_edit,
            state=WorkflowState.GENERATING_CONTENT,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Edit instruction recorded workflow_id=%s", workflow_id)
        return updated_document

    def complete_edit(
        self, workflow_id: str, *, generated_draft_reference: dict, pending_edit: dict
    ) -> WorkflowDocument:
        """Persist the outcome of a successful edit: update the lightweight
        `generated_draft` reference to the new current version, update
        `pending_edit` to record completion (kept, not cleared to `None` —
        so a late-arriving duplicate of the same Telegram update can still
        recognize the operation already finished, rather than only being
        detectable while `status` was "INSTRUCTION_RECEIVED"), clear
        `pending_retry`, and transition GENERATING_CONTENT -> SHOWING_PREVIEW.
        One atomic write — a reader can never observe the new version
        referenced without also being back in the review state."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata.pop("pending_retry", None)
        updated_document = replace(
            loaded.document,
            generated_draft=generated_draft_reference,
            pending_edit=pending_edit,
            metadata=updated_metadata,
            state=WorkflowState.SHOWING_PREVIEW,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Edit completed workflow_id=%s", workflow_id)
        return updated_document

    def fail_edit_to_preview(self, workflow_id: str) -> WorkflowDocument:
        """A permanent, non-workflow-corrupting edit failure (the request
        itself was invalid or unsupported — see
        UnsupportedEditRequestError/DraftEditValidationFailedError):
        return to SHOWING_PREVIEW with the current draft completely
        unchanged, clear `pending_edit` (the operation is over; nothing to
        resume) and `pending_retry`. Deliberately does *not* go to FAILED —
        a documented, deliberate refinement of docs/WORKFLOW.md §3.6's
        blanket "generation fails -> FAILED" rule for this one case, since
        a perfectly good current draft still exists (see
        draft_editing_service.py's module docstring)."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata.pop("pending_retry", None)
        updated_document = replace(
            loaded.document,
            pending_edit=None,
            metadata=updated_metadata,
            state=WorkflowState.SHOWING_PREVIEW,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Edit failed (non-corrupting) workflow_id=%s, returned to SHOWING_PREVIEW", workflow_id)
        return updated_document

    def set_edit_pending_retry(self, workflow_id: str) -> WorkflowDocument:
        """A retryable edit failure (timeout, rate limit, transient error):
        leaves `state` and `pending_edit` completely untouched (the
        persisted instruction is still there for /retry to reuse) and only
        sets `metadata["pending_retry"] = "draft_editing"`."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata["pending_retry"] = "draft_editing"
        updated_document = replace(loaded.document, metadata=updated_metadata, updated_at=_now_iso())
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Edit pending_retry set workflow_id=%s", workflow_id)
        return updated_document

    def approve_draft(
        self, workflow_id: str, *, generated_draft_reference: dict
    ) -> WorkflowDocument:
        """Approve action: SHOWING_PREVIEW -> COMPLETED directly, in one
        atomic write, carrying the approval metadata on the lightweight
        `generated_draft` reference (`status: "APPROVED"`,
        `approved_at`/`approved_by_telegram_user_id` — see
        draft_version_models.py's ReviewStatus). See src/workflow/states.py's
        module docstring for why this never persists a separate,
        observable `APPROVED` WorkflowState snapshot: there is no distinct
        finalization step in this system beyond this exact write. Never
        publishes anything — this only marks the post ready for a future
        publishing step, per docs/WORKFLOW.md §3.10's own note."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata.pop("pending_retry", None)
        updated_document = replace(
            loaded.document,
            generated_draft=generated_draft_reference,
            pending_edit=None,
            metadata=updated_metadata,
            state=WorkflowState.COMPLETED,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Draft approved, workflow completed workflow_id=%s", workflow_id)
        return updated_document

    def save_draft(self, workflow_id: str, *, generated_draft_reference: dict) -> WorkflowDocument:
        """Save Draft action: SHOWING_PREVIEW -> SAVED_AS_DRAFT, updating
        the lightweight `generated_draft` reference's status to
        "SAVED_AS_DRAFT" in the same atomic write. Never publishes
        anything. All persisted draft versions, the content plan, and the
        analysis remain exactly as they are — this only changes the
        workflow's own state and its one lightweight reference."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata.pop("pending_retry", None)
        updated_document = replace(
            loaded.document,
            generated_draft=generated_draft_reference,
            pending_edit=None,
            metadata=updated_metadata,
            state=WorkflowState.SAVED_AS_DRAFT,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Draft saved workflow_id=%s", workflow_id)
        return updated_document

    def reject_draft(self, workflow_id: str, *, generated_draft_reference: dict) -> WorkflowDocument:
        """Reject action: SHOWING_PREVIEW -> REJECTED, updating the
        lightweight `generated_draft` reference's status to "REJECTED" in
        the same atomic write. Never deletes any S3 object — every
        persisted draft version, the content plan, and the analysis remain
        exactly as they are, for audit/debugging, per docs/WORKFLOW.md
        §3.11."""

        loaded = self._repository.load(workflow_id)
        updated_metadata = dict(loaded.document.metadata)
        updated_metadata.pop("pending_retry", None)
        updated_document = replace(
            loaded.document,
            generated_draft=generated_draft_reference,
            pending_edit=None,
            metadata=updated_metadata,
            state=WorkflowState.REJECTED,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Draft rejected workflow_id=%s", workflow_id)
        return updated_document

    # --- Milestone 8: Publication Preparation Layer --------------------------

    def attach_publication_reference(self, workflow_id: str, publication_reference: dict | None) -> WorkflowDocument:
        """Attach (or clear) the lightweight `publication` reference — never
        the full package (caption, hashtags, media references), which stays
        solely in `publications/<workflow_id>/<output_id>.json`. Does not
        change `state` (the workflow is already `COMPLETED` by the time
        publication preparation runs) and does not touch `pending_edit` or
        `metadata` — callers manage `pending_retry` separately via
        update_metadata(), exactly like every other AI-pipeline stage."""

        loaded = self._repository.load(workflow_id)
        updated_document = replace(loaded.document, publication=publication_reference, updated_at=_now_iso())
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Workflow publication reference attached workflow_id=%s", workflow_id)
        return updated_document
