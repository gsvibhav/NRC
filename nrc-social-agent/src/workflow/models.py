"""The persisted workflow document.

One JSON object per workflow, stored at state/<workflow_id>.json (see
state_store.py). `version` lets a future milestone introduce a new shape
without breaking documents already written under an older one — from_dict()
rejects any version this code doesn't explicitly know how to read, rather
than guessing.

Version 2 (Milestone 4B) adds two fields for the adaptive clarification
engine: `clarification_context` (the structured working-context model —
see src/ai/clarification_models.py) and `pending_question` (the currently
outstanding clarification question, if any — cleared the moment it's
answered). Version 3 (Milestone 5) adds `content_plan`, a lightweight
reference to the persisted content plan (see
src/ai/content_planning_service.py) — never the full plan, which stays
solely in `plans/<workflow_id>.json`. Version 4 (Milestone 6) adds
`generated_draft`, a lightweight reference to the persisted primary draft
(see src/ai/draft_generation_service.py). It is deliberately **not**
called `draft` — that name is already taken by the pre-existing `draft`
field below, reserved since Milestone 3 for a *different*, still-future
concept (the user's explicit "Save Draft" action, docs/WORKFLOW.md §3.9);
reusing it for the generated-content-awaiting-review reference would
conflate two unrelated ideas. Version 5 (Milestone 7) adds `pending_edit`,
the durable record of an in-flight edit-instruction cycle (see
src/ai/draft_editing_service.py and src/handlers.py's EDITING-state text
handling) — mirrors `pending_question`'s precedent exactly: a small,
restart-safe structured record of "what are we waiting on / what have we
already captured," cleared or overwritten on its own natural boundaries,
never the edit instruction's full text duplicated elsewhere (that lives
once, as a conversation turn — see `conversation` below). Version 6
(Milestone 8) adds `publication`, a lightweight reference to the persisted
publication package (see src/publication/service.py) — never the full
package (caption, hashtags, media references), which stays solely in
`publications/<workflow_id>/<output_id>.json`. Deliberately attached even
though the workflow is already `COMPLETED` (terminal) by the time
publication preparation runs: nothing in this codebase's storage layer
actually locks a "terminal" document against further writes — that's a
business-level convention, not an S3 constraint — and this reference is
exactly what makes restart-safe publication-preparation catch-up possible
without a separate persisted-intent record (see README.md's "Completed
workflow lookup"). All new fields default to `None` and read back as
`None` for an older document, so older records remain readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import (
    InvalidWorkflowStateError,
    WorkflowDeserializationError,
    WorkflowVersionMismatchError,
)
from .states import WorkflowState

CURRENT_VERSION = 6
SUPPORTED_VERSIONS = {1, 2, 3, 4, 5, 6}

_REQUIRED_STRING_FIELDS = ("workflow_id", "created_at", "updated_at")


@dataclass(frozen=True)
class WorkflowDocument:
    workflow_id: str
    telegram_user_id: int
    created_at: str
    updated_at: str
    state: WorkflowState
    media: dict
    analysis: dict | None = None
    draft: dict | None = None
    conversation: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    clarification_context: dict | None = None
    pending_question: dict | None = None
    content_plan: dict | None = None
    generated_draft: dict | None = None
    pending_edit: dict | None = None
    publication: dict | None = None
    version: int = CURRENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "workflow_id": self.workflow_id,
            "telegram_user_id": self.telegram_user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "media": self.media,
            "analysis": self.analysis,
            "draft": self.draft,
            "conversation": self.conversation,
            "metadata": self.metadata,
            "clarification_context": self.clarification_context,
            "pending_question": self.pending_question,
            "content_plan": self.content_plan,
            "generated_draft": self.generated_draft,
            "pending_edit": self.pending_edit,
            "publication": self.publication,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowDocument":
        if not isinstance(data, dict):
            raise WorkflowDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_VERSIONS:
            raise WorkflowVersionMismatchError(
                f"unsupported workflow document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_VERSIONS)})"
            )

        for field_name in _REQUIRED_STRING_FIELDS:
            value = data.get(field_name)
            if not isinstance(value, str) or not value:
                raise WorkflowDeserializationError(f"missing or invalid field: {field_name!r}")

        telegram_user_id = data.get("telegram_user_id")
        if not isinstance(telegram_user_id, int) or isinstance(telegram_user_id, bool):
            raise WorkflowDeserializationError(
                f"missing or invalid field: 'telegram_user_id' (got {telegram_user_id!r})"
            )

        state_raw = data.get("state")
        try:
            state = WorkflowState(state_raw)
        except ValueError as exc:
            raise InvalidWorkflowStateError(f"unrecognized state: {state_raw!r}") from exc

        media = data.get("media")
        if not isinstance(media, dict):
            raise WorkflowDeserializationError(f"missing or invalid field: 'media' (got {media!r})")

        analysis = data.get("analysis")
        if analysis is not None and not isinstance(analysis, dict):
            raise WorkflowDeserializationError(f"invalid field: 'analysis' (got {analysis!r})")

        draft = data.get("draft")
        if draft is not None and not isinstance(draft, dict):
            raise WorkflowDeserializationError(f"invalid field: 'draft' (got {draft!r})")

        conversation = data.get("conversation")
        if not isinstance(conversation, list):
            raise WorkflowDeserializationError(f"invalid field: 'conversation' (got {conversation!r})")

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise WorkflowDeserializationError(f"invalid field: 'metadata' (got {metadata!r})")

        # Absent entirely on a version-1 document (pre-4B) — .get() defaults
        # to None either way, so both cases read identically.
        clarification_context = data.get("clarification_context")
        if clarification_context is not None and not isinstance(clarification_context, dict):
            raise WorkflowDeserializationError(
                f"invalid field: 'clarification_context' (got {clarification_context!r})"
            )

        pending_question = data.get("pending_question")
        if pending_question is not None and not isinstance(pending_question, dict):
            raise WorkflowDeserializationError(f"invalid field: 'pending_question' (got {pending_question!r})")

        content_plan = data.get("content_plan")
        if content_plan is not None and not isinstance(content_plan, dict):
            raise WorkflowDeserializationError(f"invalid field: 'content_plan' (got {content_plan!r})")

        generated_draft = data.get("generated_draft")
        if generated_draft is not None and not isinstance(generated_draft, dict):
            raise WorkflowDeserializationError(f"invalid field: 'generated_draft' (got {generated_draft!r})")

        pending_edit = data.get("pending_edit")
        if pending_edit is not None and not isinstance(pending_edit, dict):
            raise WorkflowDeserializationError(f"invalid field: 'pending_edit' (got {pending_edit!r})")

        publication = data.get("publication")
        if publication is not None and not isinstance(publication, dict):
            raise WorkflowDeserializationError(f"invalid field: 'publication' (got {publication!r})")

        return cls(
            workflow_id=data["workflow_id"],
            telegram_user_id=telegram_user_id,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            state=state,
            media=media,
            analysis=analysis,
            draft=draft,
            conversation=conversation,
            metadata=metadata,
            clarification_context=clarification_context,
            pending_question=pending_question,
            content_plan=content_plan,
            generated_draft=generated_draft,
            pending_edit=pending_edit,
            publication=publication,
            version=version,
        )
