"""Exceptions for the Claude adapter and analysis pipeline.

Every exception carries a `user_message` — short and generic, mirroring the
pattern already used in src/media/errors.py, src/workflow/errors.py, and
src/conversation/errors.py. Callers show `user_message` to the Telegram
user and log the exception itself for diagnosis. Never surface model
names, token counts, request IDs, or raw API responses to the user.
"""

from __future__ import annotations


class AnalysisError(Exception):
    """Base class for every expected failure in the analysis pipeline."""

    user_message = "The media was uploaded, but analysis could not be completed. Please try again later."

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# --- Claude adapter-level errors (client.py) --------------------------------


class ClaudeConfigurationError(AnalysisError):
    """Invalid Anthropic client configuration. Should only be reachable at
    startup, before any request is attempted."""


class ClaudeAuthenticationError(AnalysisError):
    """The Anthropic API rejected our credentials. Not retryable."""


class ClaudeRetryableError(AnalysisError):
    """Common base for Claude adapter failures where the SDK's own
    client-level retries (see client.py) were already exhausted, but the
    *workflow* is not considered terminal: the caller (analysis_service.py
    / clarification_service.py) keeps the workflow active and the
    Telegram active-workflow pointer set, and marks it retryable via
    `WorkflowManager.update_metadata(..., {"pending_retry": ...})` so
    `/retry` (see handlers.py) can safely re-attempt the same step.
    Distinct from ClaudePermanentError and the other non-retryable errors
    below, which move the workflow straight to FAILED."""

    user_message = (
        "That's taking longer than expected and couldn't complete right now. "
        "Please try again in a moment, or send /retry."
    )


class ClaudeRateLimitError(ClaudeRetryableError):
    """Rate limited, and the SDK's own built-in retries were exhausted."""


class ClaudeTimeoutError(ClaudeRetryableError):
    """The request timed out, and the SDK's own built-in retries were
    exhausted."""


class ClaudeTransientError(ClaudeRetryableError):
    """A retryable server-side failure (5xx, connection error) that
    persisted after the SDK's own built-in retries were exhausted."""


class ClaudePermanentError(AnalysisError):
    """A non-retryable request failure (e.g. a 400 the SDK never retries)."""


class AnalysisRefusedError(AnalysisError):
    """Claude declined to fulfill the request (stop_reason == "refusal").
    Shared by both the media-analysis and clarification pipelines. Not
    retryable with the same request."""


# --- Response validation errors (parser.py) ---------------------------------


class MalformedAnalysisResponseError(AnalysisError):
    """Claude's response body wasn't valid JSON at all."""


class AnalysisResponseSchemaMismatchError(AnalysisError):
    """The response was valid JSON but didn't match the required
    structure (missing/wrong-typed/extra fields). Never partially
    accepted or silently repaired."""


# --- Media eligibility errors (analysis_service.py) --------------------------


class UnsupportedMediaTypeForAnalysisError(AnalysisError):
    """This media type (e.g. video) isn't analyzable in this milestone."""

    user_message = (
        "Photo analysis is available now — video analysis isn't supported yet. "
        "Your video was uploaded and saved."
    )


class MediaTooLargeForAnalysisError(AnalysisError):
    """The media exceeds Claude's own safe submission size limit."""

    user_message = "That file is too large for analysis right now. Your media was uploaded and saved."


class MediaRetrievalError(AnalysisError):
    """Failed to read the already-uploaded media back from S3."""


# --- Persistence errors (analysis_repository.py / analysis_manager.py) ------


class AnalysisNotFoundError(AnalysisError):
    user_message = "I couldn't find that analysis."


class AnalysisValidationError(AnalysisError):
    """Base class for a stored analysis document that isn't structurally
    valid."""


class AnalysisDeserializationError(AnalysisValidationError):
    pass


class AnalysisSerializationError(AnalysisValidationError):
    pass


class AnalysisVersionMismatchError(AnalysisValidationError):
    pass


class InvalidAnalysisStatusError(AnalysisValidationError):
    pass


class AnalysisPersistenceError(AnalysisError):
    pass


class AnalysisConcurrentModificationError(AnalysisError):
    user_message = "That post's analysis was just updated elsewhere. Please try again."


# --- Clarification pipeline errors (clarification_parser.py / question_validation.py / clarification_service.py) ---


class ClarificationError(AnalysisError):
    """Base class for failures specific to the adaptive clarification
    pipeline. Still an AnalysisError, so existing broad `except
    AnalysisError` handling in handlers.py catches these too."""


class MalformedClarificationResponseError(ClarificationError):
    """Claude's clarification-decision response body wasn't valid JSON."""


class ClarificationResponseSchemaMismatchError(ClarificationError):
    """The response was valid JSON but didn't match the required
    structure. Never partially accepted."""


class InvalidClarificationQuestionError(ClarificationError):
    """A generated question failed quality/safety validation (see
    question_validation.py) even after one controlled regeneration
    attempt. Never sent to the user."""


class WorkflowNotWaitingForReplyError(ClarificationError):
    """A plain-text reply arrived, but the resolved workflow isn't in
    WAITING_FOR_USER — the reply is not processed as a clarification
    answer."""


class NoPendingQuestionError(ClarificationError):
    """The workflow is WAITING_FOR_USER but has no `pending_question` on
    record — a data-consistency problem, not a user-facing situation that
    should occur in normal operation."""


class ClarificationContextInsufficientError(ClarificationError):
    """MAX_CLARIFICATION_QUESTIONS was reached and Claude still assessed
    the context as unsafe to generate from. The workflow moves to FAILED
    rather than fabricating certainty — see clarification_service.py."""

    user_message = (
        "I still don't have enough information to do this justice. "
        "Please start over with /cancel and a new upload, adding a bit more detail in your first message."
    )


# --- Content planning pipeline errors (content_plan_parser.py / content_plan_validation.py / content_planning_service.py) ---


class ContentPlanError(AnalysisError):
    """Base class for failures specific to the Content Planning Engine
    (Milestone 5). Still an AnalysisError, so existing broad `except
    AnalysisError` handling in handlers.py catches these too."""

    user_message = (
        "I analyzed everything I need, but couldn't finish planning the content for this post. "
        "Please try again later."
    )


class MalformedContentPlanResponseError(ContentPlanError):
    """Claude's content-plan response body wasn't valid JSON."""


class ContentPlanSchemaMismatchError(ContentPlanError):
    """The response was valid JSON but didn't match the required
    structure. Never partially accepted."""


class ContentPlanValidationFailedError(ContentPlanError):
    """The plan was structurally valid JSON but failed a business-mandated
    quality/safety rule (see content_plan_validation.py) — too generic,
    final-copy leakage, duplicate/unsupported output types, invalid
    priorities, output count outside the configured range, an
    excluded/proposed output-type conflict, etc. Never partially accepted
    or silently repaired; one bounded regeneration attempt is made in
    content_planning_service.py before this becomes a permanent failure."""


class WorkflowNotEligibleForPlanningError(ContentPlanError):
    """The workflow isn't in GENERATING_CONTENT — planning is not started
    for WAITING_FOR_USER, FAILED, REJECTED, SAVED_AS_DRAFT, COMPLETED, or
    any state other than the one clarification hands off to."""


class MissingAnalysisForPlanningError(ContentPlanError):
    """No completed media analysis exists for this workflow — planning
    cannot ground itself in the media without it. Should be unreachable in
    normal operation (GENERATING_CONTENT is only reached after a
    completed analysis), so this is a data-consistency signal, not an
    expected user-facing situation."""


# --- Persistence errors (content_plan_repository.py / content_plan_manager.py) ---


class ContentPlanNotFoundError(ContentPlanError):
    user_message = "I couldn't find that content plan."


class ContentPlanPersistedValidationError(ContentPlanError):
    """Base class for a stored content-plan document that isn't
    structurally valid."""


class ContentPlanDeserializationError(ContentPlanPersistedValidationError):
    pass


class ContentPlanSerializationError(ContentPlanPersistedValidationError):
    pass


class ContentPlanVersionMismatchError(ContentPlanPersistedValidationError):
    pass


class InvalidContentPlanStatusError(ContentPlanPersistedValidationError):
    pass


class ContentPlanPersistenceError(ContentPlanError):
    pass


class ContentPlanConcurrentModificationError(ContentPlanError):
    user_message = "That post's content plan was just updated elsewhere. Please try again."


# --- Primary draft generation pipeline errors (draft_parser.py / draft_validation.py / draft_generation_service.py) ---


class DraftGenerationError(AnalysisError):
    """Base class for failures specific to the Primary Draft Generation
    Engine (Milestone 6). Still an AnalysisError, so existing broad
    `except AnalysisError` handling in handlers.py catches these too."""

    user_message = (
        "I planned the content, but couldn't finish preparing a draft for review. "
        "Please try again later."
    )


class MalformedDraftResponseError(DraftGenerationError):
    """Claude's draft response body wasn't valid JSON, or arrived wrapped
    in markdown/prose instead of a bare structured object. Also raised by
    draft_parser.py's parse_draft_response() when reused for an *edit*
    response (Milestone 7, draft_editing_service.py) — the response shape
    is identical, so the same parser and the same error type are shared
    rather than duplicated."""


class DraftResponseSchemaMismatchError(DraftGenerationError):
    """The response was valid JSON but didn't match the required
    structure for the selected output type. Never partially accepted.
    Also raised for a mismatched *edit* response — see
    MalformedDraftResponseError's note above."""


class DraftValidationFailedError(DraftGenerationError):
    """The draft was structurally valid JSON but failed a business-mandated
    quality/safety rule (see draft_validation.py) — too generic, an
    unsupported claim, a length violation, a disallowed CTA, excessive
    hashtags/emoji, internal terminology, etc. One bounded regeneration
    attempt is made in draft_generation_service.py before this becomes a
    permanent failure."""


class NoPrimaryOutputError(DraftGenerationError):
    """The completed content plan doesn't have exactly one priority-1
    output (zero, or more than one) — a data-consistency signal, since
    content_plan_validation.py already enforces exactly one at plan-creation
    time; should be unreachable in normal operation."""


class UnsupportedGenerationOutputTypeError(DraftGenerationError):
    """The unique priority-1 output's type does not have
    `supports_generation = True` in the registry. Fails safely rather than
    substituting a different (e.g. secondary) output — see
    draft_generation_service.py's module docstring."""

    user_message = (
        "I've planned the content, but generating a draft for that output type isn't supported yet. "
        "Your plan is saved — you can revisit this once that format is supported."
    )


class WorkflowNotEligibleForDraftGenerationError(DraftGenerationError):
    """The workflow isn't in GENERATING_CONTENT, or has no completed
    content plan — draft generation is not started outside that
    precondition."""


class MissingContentPlanForGenerationError(DraftGenerationError):
    """No completed content plan exists for this workflow — generation
    cannot select a primary output without it. Should be unreachable in
    normal operation (GENERATING_CONTENT's draft-generation step only runs
    after a completed plan), so this is a data-consistency signal."""


# --- Persistence errors (draft_repository.py / draft_manager.py) ---


class DraftNotFoundError(DraftGenerationError):
    user_message = "I couldn't find that draft."


class DraftPersistedValidationError(DraftGenerationError):
    """Base class for a stored draft document that isn't structurally
    valid."""


class DraftDeserializationError(DraftPersistedValidationError):
    pass


class DraftSerializationError(DraftPersistedValidationError):
    pass


class DraftVersionMismatchError(DraftPersistedValidationError):
    pass


class InvalidDraftStatusError(DraftPersistedValidationError):
    pass


class DraftPersistenceError(DraftGenerationError):
    pass


class DraftConcurrentModificationError(DraftGenerationError):
    user_message = "That draft was just updated elsewhere. Please try again."


# --- Draft review, editing, and versioning errors (Milestone 7) ---
# (draft_version_repository.py / draft_version_manager.py / draft_edit_prompts.py /
#  draft_edit_validation.py / draft_editing_service.py / handlers.py's review_action())


class DraftReviewError(AnalysisError):
    """Base class for failures specific to the Telegram Draft Review,
    Editing and Approval Workflow (Milestone 7). Still an AnalysisError, so
    existing broad `except AnalysisError` handling in handlers.py catches
    these too."""

    user_message = "I couldn't complete that action right now. Please try again later."


# --- Immutable version / current-pointer persistence -------------------


class DraftVersionNotFoundError(DraftReviewError):
    """No immutable version record exists at the requested
    (workflow_id, output_id, version_number)."""

    user_message = "I couldn't find that draft version."


class DraftVersionPersistedValidationError(DraftReviewError):
    """Base class for a stored version or current-pointer document that
    isn't structurally valid."""


class DraftVersionDeserializationError(DraftVersionPersistedValidationError):
    pass


class DraftVersionSerializationError(DraftVersionPersistedValidationError):
    pass


class DraftVersionDocumentVersionMismatchError(DraftVersionPersistedValidationError):
    """The persisted version/pointer envelope's own `version` field (the
    document schema version) isn't one this code knows how to read. Named
    distinctly from the pre-existing `DraftVersionMismatchError`
    (draft_models.py's document-schema-version check) to avoid confusion
    with this module's *draft version number* concept — two unrelated
    meanings of the word "version" that collide in this milestone's
    vocabulary, so the class names spell out the distinction explicitly."""


class DraftVersionPersistenceError(DraftReviewError):
    """Any other S3 read/write failure while handling an immutable version
    or the current-version pointer."""


class DraftVersionConflictError(DraftReviewError):
    """A conditional write lost a race while creating the next immutable
    version or advancing the current-version pointer — see
    draft_version_manager.py's module docstring for how an orphaned
    immutable version (created but never became current) is handled. Not
    automatically retried; the caller reports a concurrency conflict and
    the operation must be re-attempted from scratch (which will itself
    re-check for an already-completed operation first)."""

    user_message = "That draft was just updated elsewhere. Please try again."


class CurrentDraftPointerNotFoundError(DraftReviewError):
    """No current-version pointer exists yet for this (workflow_id,
    output_id) and no legacy Milestone-6 draft exists to migrate from
    either — a data-consistency signal, should be unreachable once a
    workflow has reached SHOWING_PREVIEW."""

    user_message = "I couldn't find that draft."


# --- Review-action eligibility -------------------------------------------


class WorkflowNotEligibleForReviewActionError(DraftReviewError):
    """The workflow isn't in a state where Approve/Edit/Save/Reject is a
    valid action (must be SHOWING_PREVIEW) — raised with **no side
    effects**, exactly like the other "not eligible yet" errors in this
    pipeline."""


class StaleDraftVersionError(DraftReviewError):
    """The Telegram callback (or /retry) referenced a version number that
    is no longer the authoritative current version — e.g. a stale inline
    button from a superseded preview message. The workflow and draft are
    left completely unchanged; no Claude call, no S3 write, no state
    transition."""

    user_message = "A newer version of this draft is already available — please use the latest preview."


class MalformedReviewActionError(DraftReviewError):
    """Telegram callback_data for a review action didn't parse into a
    recognized action/workflow-reference/version triple. Never trusted or
    partially interpreted."""


# --- Edit generation -----------------------------------------------------


class NoPendingEditInstructionError(DraftReviewError):
    """The workflow is in EDITING (or GENERATING_CONTENT for an edit) but
    has no persisted `pending_edit` record — a data-consistency signal,
    should be unreachable in normal operation."""


class DraftEditValidationFailedError(DraftReviewError):
    """The revised draft was structurally valid JSON but failed a
    business-mandated quality/safety/instruction-alignment rule (see
    draft_edit_validation.py). One bounded corrective attempt is made in
    draft_editing_service.py before this becomes a permanent (but
    non-workflow-corrupting) failure — the current draft is left
    unchanged and the workflow returns to SHOWING_PREVIEW, per this
    milestone's explicit refinement of docs/WORKFLOW.md §3.6's blanket
    "generation fails -> FAILED" rule (see draft_editing_service.py's
    module docstring)."""


class UnsupportedEditRequestError(DraftReviewError):
    """The user's instruction asked for something outside the current
    review's scope (e.g. "turn this into a LinkedIn post" — a platform/
    output-type switch). Rejected without corrupting the workflow: the
    current version is left unchanged and the workflow returns to
    SHOWING_PREVIEW, exactly like DraftEditValidationFailedError."""

    user_message = (
        "I can't change the platform or format during this review — this is a review of the "
        "Instagram caption already planned. I've left your current draft unchanged."
    )


class WorkflowNotEligibleForEditError(DraftReviewError):
    """The workflow isn't in EDITING/GENERATING_CONTENT with a pending
    edit instruction — editing is not started outside that precondition."""


class MissingCurrentDraftForEditError(DraftReviewError):
    """No current draft version exists for this workflow — editing cannot
    ground itself without one. Should be unreachable in normal operation
    (EDITING is only reached from SHOWING_PREVIEW, which requires a
    current draft to exist)."""
