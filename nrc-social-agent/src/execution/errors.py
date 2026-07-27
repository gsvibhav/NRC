"""Exceptions for the Publication Execution Layer (Milestones 9-10).

A dedicated, top-level error hierarchy — mirroring src/publication/errors.py's
own precedent (which itself mirrors workflow/conversation/media) — rather
than folding into the publication domain's own errors. Execution is its
own domain: it reads a publication package but never mutates it, and it
never calls Claude or any platform API.

Same retryable/permanent split rationale as src/publication/errors.py:
`ExecutionPermanentError` subclasses are the enumerated, specific
business-logic conditions (publication package missing/not ready,
unsupported channel, failed validation) — never retried automatically.
Everything else under the generic `ExecutionError` base (persistence
errors, concurrent-modification conflicts, a workflow-load failure) is
treated as retryable by default, since there is no Claude call here to
mirror a retryable/permanent split from.
"""

from __future__ import annotations


class ExecutionError(Exception):
    """Base class for every expected failure in execution creation."""

    user_message = (
        "Your content is prepared, but I couldn't queue it for publishing right now. "
        "Nothing has been published. You can retry this step."
    )

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ExecutionPermanentError(ExecutionError):
    """Base for a specific, enumerated, non-retryable execution-creation
    failure. The publication package (and the underlying approved draft)
    are never touched — only the execution-domain record, if any,
    reflects the failure."""

    user_message = (
        "Your content is prepared, but this output can't be queued for publishing yet."
    )


# --- package resolution / linkage -------------------------------------------


class PublicationPackageNotReadyForExecutionError(ExecutionPermanentError):
    """The workflow has no `publication` reference, or that reference is
    not `READY_FOR_PUBLISHING` — execution is never created from a
    missing, failed, or not-yet-prepared package."""

    user_message = "There's no publishing package ready yet for that post."


class PublicationPackageNotFoundForExecutionError(ExecutionPermanentError):
    """The workflow's lightweight `publication` reference names a package
    that can't actually be loaded from the publication domain — a
    data-consistency signal, should be unreachable in normal operation."""


class ExecutionLinkageMismatchError(ExecutionPermanentError):
    """The loaded publication package's own identity doesn't match the
    workflow's cached reference — a data-consistency signal, should be
    unreachable since the reference is only ever written from the
    package itself."""


class UnsupportedExecutionChannelError(ExecutionPermanentError):
    """The package's channel has no known publisher mapping (see
    models.py's resolve_publisher()) — fails safely rather than guessing
    a default publisher."""

    user_message = "That output's channel isn't supported for publishing yet."


class ExecutionValidationFailedError(ExecutionPermanentError):
    """The constructed execution document failed a business-mandated
    validation rule (see validation.py)."""


# --- dispatch (Milestone 10) -------------------------------------------------


class ExecutionNotEligibleForDispatchError(ExecutionPermanentError):
    """The execution isn't in a state dispatch can start/resume from
    (e.g. already COMPLETED via a different path, or a channel/publisher
    that no longer resolves) — a data-consistency signal in normal
    operation, since the dispatch service itself gates every entry point."""


class UnauthorizedDispatchError(ExecutionPermanentError):
    """The calling Telegram user does not own the workflow this execution
    belongs to — checked by loading the workflow directly (never trusting
    a client-supplied identifier), exactly like every other review action
    in this codebase. Zero Meta calls occur before this check passes."""

    user_message = "That action isn't available to you."


class MissingDispatchAuthorizationError(ExecutionPermanentError):
    """Dispatch was attempted before an explicit Publish authorization was
    persisted — should be unreachable, since the one dispatch entry point
    (`authorize_and_dispatch()`) always persists authorization first."""


class UnknownPublisherError(ExecutionPermanentError):
    """The execution/publication's `publisher` value has no registered
    implementation — fails safely rather than guessing a default."""

    user_message = "That publishing channel isn't supported yet."


class PublisherChannelMismatchError(ExecutionPermanentError):
    """The execution's own channel doesn't match the authoritative
    publication package's channel — a data-consistency signal, should be
    unreachable since both are derived from the same package at creation
    time."""


# --- persisted-document validation ------------------------------------------


class ExecutionPersistedValidationError(ExecutionError):
    """Base class for a stored execution document that isn't structurally
    valid."""


class ExecutionDeserializationError(ExecutionPersistedValidationError):
    pass


class ExecutionSerializationError(ExecutionPersistedValidationError):
    pass


class ExecutionVersionMismatchError(ExecutionPersistedValidationError):
    pass


class InvalidExecutionStatusError(ExecutionPersistedValidationError):
    pass


class InvalidDispatchCheckpointError(ExecutionPersistedValidationError):
    pass


# --- persistence / lookup ---------------------------------------------------


class ExecutionNotFoundError(ExecutionError):
    user_message = "I couldn't find that execution record."


class ExecutionPersistenceError(ExecutionError):
    """Any other S3 read/write failure. Treated as retryable by the
    service — see this module's docstring."""


# --- controlled live validation (Milestone 11B) ------------------------------


class LiveValidationError(ExecutionError):
    """Base class for every expected failure in the controlled Instagram
    live-validation flow (src/execution/live_validation.py). Deliberately
    a separate hierarchy root from ExecutionPermanentError/dispatch
    errors above — a live-validation failure never means the underlying
    execution or package is broken, only that this specific, extra-
    cautious confirmation gate could not be satisfied right now."""

    user_message = "That live-validation action isn't available right now."


class NotLiveTestOperatorError(LiveValidationError):
    """The calling Telegram user is not in INSTAGRAM_LIVE_TEST_OPERATOR_IDS
    — checked in addition to, never instead of, the general @restricted
    allowlist check."""

    user_message = "That action isn't available to you."


class NoEligibleLiveValidationCandidateError(LiveValidationError):
    """No active workflow, or the active workflow's publication/execution
    doesn't currently meet every controlled-live-validation eligibility
    rule (see live_validation.py's _check_eligibility())."""


class LiveValidationConfirmationNotFoundError(LiveValidationError):
    """No AWAITING_CONFIRMATION live-validation state exists for this
    publication_id/token pair — either none was ever created, a newer
    preview superseded it, or it was already consumed/cancelled."""

    user_message = "That live-validation confirmation has already been used or has expired. Please request a new preview."


class LiveValidationConfirmationExpiredError(LiveValidationError):
    user_message = "That live-validation confirmation has expired. Please request a new preview."


class LiveValidationStateChangedError(LiveValidationError):
    """The execution, package, or Instagram account no longer matches
    what the operator was shown at preview time (version/checkpoint/
    status/package digest/account fingerprint mismatch) — never
    dispatched; the operator must review a fresh preview."""

    user_message = "The publication or account changed since this preview was shown. Please request a new preview."


class LiveValidationNotReadyError(LiveValidationError):
    """Instagram diagnostics did not report `instagram_ready=True` at
    (re-)validation time — never dispatched."""

    user_message = "Instagram is not currently ready for a live publication — check /instagram_status."


class UnsupportedLiveValidationMediaError(LiveValidationError):
    """The candidate package's placement/media isn't the single-JPEG-Feed
    scope this milestone deliberately limits controlled live validation
    to (see README.md's Milestone 11B section) — Reels and any other
    media are out of scope until a later milestone explicitly extends it."""

    user_message = "Only a single approved JPEG Feed image is supported for controlled live validation right now."


class ExecutionConcurrentModificationError(ExecutionError):
    """A conditional write lost a race. Two distinct callers reuse this
    same exception for two distinct reconciliation strategies: `service.py`
    reconciles a losing *creation* attempt (Milestone 9) by re-reading the
    winner's record; `dispatch_service.py` reconciles a losing *claim* or
    *checkpoint* write (Milestone 10) by reloading the authoritative
    execution and re-evaluating from its actual current state — never
    assumed stale, never blindly retried against the caller's own
    now-outdated in-memory copy."""

    user_message = "That publication is already being processed. Please try again shortly."
