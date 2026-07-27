"""Exceptions for the Publication Preparation Layer (Milestone 8).

A dedicated, top-level error hierarchy — mirroring src/workflow/errors.py,
src/conversation/errors.py, and src/media/errors.py's identical pattern —
rather than folding into src/ai/errors.py's `AnalysisError` hierarchy.
This is deliberate: publication preparation makes zero Claude calls and is
not part of the AI pipeline; it is its own domain, exactly like workflow
lifecycle or media ingestion are their own domains.

Every exception carries a `user_message` — short and generic. Callers show
`user_message` to the Telegram user and log the exception itself for
diagnosis.

Two families, matching this milestone's own retryable/permanent split
(distinct from the Claude-call-based split used everywhere else in this
codebase, since there is no Claude call here to be retryable or not):

- `PublicationPermanentError` subclasses are the enumerated, specific
  business-logic conditions (unsupported output/media type, missing
  approval metadata, corrupt draft/plan linkage, failed validation) —
  never retried automatically; the underlying approved draft is always
  left completely untouched.
- Everything else under the `PublicationError` base (persistence errors,
  concurrent-modification conflicts, a workflow-load failure) is treated
  as retryable by default — see src/publication/service.py's module
  docstring for the reasoning.
"""

from __future__ import annotations


class PublicationError(Exception):
    """Base class for every expected failure in publication preparation."""

    user_message = (
        "Approved, but I couldn't prepare it for publishing right now. Nothing has been published. "
        "You can retry this step."
    )

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PublicationPermanentError(PublicationError):
    """Base for a specific, enumerated, non-retryable preparation failure.
    The underlying approval and approved draft are never touched — only
    the publication-domain record reflects the failure."""

    user_message = (
        "The draft is approved, but this output isn't supported for publishing preparation yet."
    )


# --- eligibility / linkage --------------------------------------------------


class WorkflowNotEligibleForPublicationError(PublicationPermanentError):
    """The workflow isn't COMPLETED with an APPROVED generated-draft
    reference — publication preparation is not started outside that
    precondition."""

    user_message = "That workflow isn't approved, so there's nothing to prepare for publishing."


class MissingApprovalMetadataForPublicationError(PublicationPermanentError):
    """The workflow is COMPLETED/APPROVED but its `generated_draft`
    reference is missing required approval fields — a data-consistency
    signal, should be unreachable in normal operation."""


class ApprovedDraftVersionNotFoundError(PublicationPermanentError):
    """No current draft version could be resolved for the approved
    output — should be unreachable, since approval only ever happens
    against an existing current version."""


class ApprovedVersionLinkageMismatchError(PublicationPermanentError):
    """The current-version pointer's own status/identity doesn't match
    what the workflow's approval metadata recorded — a data-consistency
    signal, should be unreachable given approval is immutable per
    workflow (nothing can edit a draft again after it's been approved)."""


class MissingContentPlanForPublicationError(PublicationPermanentError):
    """No completed content plan exists for this workflow — should be
    unreachable, since an approved draft can only exist downstream of one."""


class NoPrimaryOutputForPublicationError(PublicationPermanentError):
    """The completed content plan has no output matching the approved
    output_id — a data-consistency signal, should be unreachable."""


class UnsupportedPublicationOutputTypeError(PublicationPermanentError):
    """The approved output's type has no publication-preparation channel
    mapping (see models.py's resolve_channel()) — fails safely rather
    than guessing or falling back to a default channel."""

    user_message = (
        "The draft is approved, but this output type isn't supported for publishing preparation yet."
    )


class UnsupportedPublicationMediaTypeError(PublicationPermanentError):
    """The workflow's media doesn't have a type this milestone knows how
    to reference for publication."""


class MissingMediaForPublicationError(PublicationPermanentError):
    """The workflow has no durable media reference to include in the
    package — should be unreachable, since every workflow has media by
    construction (a workflow cannot exist without an original upload)."""


class PublicationValidationFailedError(PublicationPermanentError):
    """The built package failed a business-mandated validation rule (see
    validation.py) — an internal-consistency or contract-minimality
    problem, never silently repaired or persisted."""


# --- persisted-document validation ------------------------------------------


class PublicationPersistedValidationError(PublicationError):
    """Base class for a stored publication package that isn't
    structurally valid."""


class PublicationDeserializationError(PublicationPersistedValidationError):
    pass


class PublicationSerializationError(PublicationPersistedValidationError):
    pass


class PublicationVersionMismatchError(PublicationPersistedValidationError):
    pass


class InvalidPublicationStatusError(PublicationPersistedValidationError):
    pass


# --- persistence / lookup ---------------------------------------------------


class PublicationNotFoundError(PublicationError):
    user_message = "I couldn't find that publication package."


class PublicationPersistenceError(PublicationError):
    """Any other S3 read/write failure. Treated as retryable by the
    service — see this module's docstring."""


class PublicationConcurrentModificationError(PublicationError):
    """A conditional write lost a race — either a duplicate creation
    attempt, or (defensively) an attempted update of an existing package.
    Treated as retryable — a fresh attempt re-checks for an existing,
    already-successful package first."""

    user_message = "That publication package was just updated elsewhere. Please try again."
