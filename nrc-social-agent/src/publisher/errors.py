"""Exceptions for the generic publisher layer (Milestone 10): the
platform-neutral contract a specific adapter (e.g. `instagram/publisher.py`)
raises, and `src/execution/dispatch_service.py` catches and normalizes
into a persisted `PublisherFailure` on the execution document.

Three families — deliberately distinct from the retryable/permanent
split used everywhere else in this codebase, because a real platform
call introduces a genuinely new third case a Claude call never has: an
**ambiguous** outcome, where the request may have reached the platform
but no reliable response was received. Ambiguous must never be treated
as ordinary-retryable (that could cause a second, duplicate publish) or
as ordinary-permanent (that could give up on content Meta already
published). See dispatch_service.py's own docstring for the recovery
rules this distinction exists to enable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import PublisherFailure


class PublisherError(Exception):
    """Base class for every expected failure a Publisher adapter raises.
    `failure`, when provided, is the already-normalized
    `PublisherFailure` the dispatch service persists verbatim — built by
    error_mapping.py so callers never construct one ad hoc."""

    def __init__(self, detail: str, *, failure: "PublisherFailure | None" = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.failure = failure


class PublisherPermanentError(PublisherError):
    """A specific, enumerated, non-retryable condition determined with
    certainty — invalid credentials, unsupported media, rejected
    content, an execution/publication mismatch. Never retried
    automatically.

    `reset_checkpoint`, when set, tells the dispatch service the durable
    platform-progress checkpoint must be reset (e.g. back to
    `NOT_STARTED`) rather than preserved — used only for the narrow case
    where the failure means the in-flight platform state itself is dead
    and unusable (a container that has expired or errored out), so a
    future retry must start a fresh one rather than repeatedly polling a
    container that can never succeed."""

    def __init__(self, detail: str, *, failure=None, reset_checkpoint=None) -> None:
        super().__init__(detail, failure=failure)
        self.reset_checkpoint = reset_checkpoint


class PublisherRetryableError(PublisherError):
    """A transient condition safe to retry from the same checkpoint —
    network failure *before* a request was accepted, rate limiting, a
    temporary platform-service error, or a bounded container-processing
    timeout. Never itself indicates the platform received and possibly
    acted upon an irreversible request."""


class PublisherAmbiguousError(PublisherError):
    """The request may have reached the platform, but no reliable
    response was received (a timeout or connection loss during the
    irreversible publish call, or a successful-looking HTTP status whose
    body couldn't be parsed). Carries whatever partial evidence is
    available via `partial_platform_state` (e.g. a `container_id`) so the
    dispatch service can attempt reconciliation — querying a safe,
    read-only endpoint for evidence of what actually happened — before
    ever repeating the irreversible call."""

    def __init__(self, detail: str, *, partial_platform_state: dict | None = None) -> None:
        super().__init__(detail)
        self.partial_platform_state = partial_platform_state or {}
