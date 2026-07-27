"""Platform-neutral result/failure models the generic Publisher contract
returns. These are what makes the contract "not expose raw HTTP-library
objects, not return secrets" true structurally: `PublisherResult` and
`PublisherFailure` are plain, narrow dataclasses with a fixed field set —
there is no way for a raw `requests.Response`, an access token, or a full
Meta error payload to travel through them, because neither dataclass has
a field wide enough to hold one.

`src/execution/dispatch_service.py` converts `.to_dict()` output
directly into the execution document's own `result`/`failure` fields
(see src/execution/models.py) — the shapes are deliberately identical to
what's persisted there, so no separate normalization step is needed at
that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..execution.models import DispatchCheckpoint


@dataclass(frozen=True)
class PublisherResult:
    """Normalized success — see this module's docstring for why no wider
    field exists here for a raw platform response."""

    platform: str
    external_media_id: str | None
    external_container_id: str | None
    permalink: str | None
    published_at: str
    verified_at: str | None
    media_type: str

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "external_media_id": self.external_media_id,
            "external_container_id": self.external_container_id,
            "permalink": self.permalink,
            "published_at": self.published_at,
            "verified_at": self.verified_at,
            "media_type": self.media_type,
        }


class FailureCategory(str, Enum):
    """A small, closed set of safe, platform-neutral failure categories
    — never a raw platform error code/message on its own. See
    src/publisher/instagram/error_mapping.py for how real Meta error
    codes/subcodes are mapped into these."""

    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_PLATFORM_ERROR = "TRANSIENT_PLATFORM_ERROR"
    CONTAINER_PROCESSING_TIMEOUT = "CONTAINER_PROCESSING_TIMEOUT"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    MISSING_PERMISSION = "MISSING_PERMISSION"
    ACCOUNT_NOT_ELIGIBLE = "ACCOUNT_NOT_ELIGIBLE"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    INVALID_MEDIA_SOURCE = "INVALID_MEDIA_SOURCE"
    INVALID_CAPTION = "INVALID_CAPTION"
    CONTAINER_REJECTED = "CONTAINER_REJECTED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    AMBIGUOUS_PUBLISH_OUTCOME = "AMBIGUOUS_PUBLISH_OUTCOME"
    UNKNOWN_PLATFORM_ERROR = "UNKNOWN_PLATFORM_ERROR"


@dataclass(frozen=True)
class PublisherFailure:
    """Normalized failure — see this module's docstring for why no wider
    field exists here for an access token, a full request URL, full
    headers, a raw error payload, or a stack trace."""

    category: FailureCategory
    retryable: bool
    operation: str
    safe_message: str
    occurred_at: str
    platform_code: int | None = None
    platform_subcode: int | None = None
    retry_after_seconds: int | None = None

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "retryable": self.retryable,
            "platform_code": self.platform_code,
            "platform_subcode": self.platform_subcode,
            "operation": self.operation,
            "safe_message": self.safe_message,
            "occurred_at": self.occurred_at,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class PublisherStepResult:
    """One `Publisher.advance()` call's outcome. Never itself persisted —
    `dispatch_service.py` translates it into the specific
    `ExecutionManager` write appropriate to what happened (a checkpoint
    advance, a terminal success, or a terminal/retryable failure)."""

    next_checkpoint: DispatchCheckpoint
    platform_state_update: dict = field(default_factory=dict)
    result: PublisherResult | None = None
    failure: PublisherFailure | None = None

    def is_terminal(self) -> bool:
        return self.result is not None or self.failure is not None
