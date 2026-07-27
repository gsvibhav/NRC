"""Maps a `MetaHttpError` (client.py) into one of the three publisher-
level outcomes (`PublisherRetryableError`/`PublisherPermanentError`/
`PublisherAmbiguousError`), using officially-documented Graph API error
codes — never classified by HTTP status alone.

**Verified against Meta's Graph API error-handling documentation**
(see the completion report for the exact page and codes reviewed):

- Retryable (transient/throttling): codes 1, 2, 4, 17, 341, 368 —
  "temporary issue due to downtime/throttling; wait and retry."
- Permanent (auth/permission): codes 10, 190, and the 200-299 range —
  "permission is either not granted or has been removed" / "access
  token expired."
- Code 100 (invalid parameter) and 506 (duplicate post) are also
  well-documented Graph API codes and are treated as permanent here:
  retrying an invalid-parameter or duplicate-content request without a
  real change would fail identically.

**Ambiguous is decided by call site, not error code**: only
`publish_media()` — the one irreversible operation — can produce an
ambiguous outcome, and only when no reliable response was received at
all (a connection failure/timeout, or a successful-looking response body
that couldn't be parsed). A definitive error response *to* `publish_media`
(Meta responded with a real error code) is not ambiguous — Meta was
reached and gave a real answer, so it's classified exactly like any
other operation's error.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from ..models import FailureCategory, PublisherFailure
from .client import MetaHttpError

_RETRYABLE_CODES = {1, 2, 4, 17, 341, 368}
_PERMANENT_CODES = {10, 190, 100, 506}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_after_seconds(error_body: dict) -> int | None:
    # Meta does not consistently expose a machine-readable retry-after
    # value in the error body for these operations; when absent, the
    # dispatch service falls back to its own configured backoff, never
    # inferring one from unrelated fields.
    value = error_body.get("error", {}).get("error_data", {}).get("retry_after_seconds") if error_body else None
    return value if isinstance(value, int) else None


def map_meta_http_error(exc: MetaHttpError, *, operation: str, is_irreversible: bool, partial_platform_state: dict | None = None) -> Exception:
    """Returns (does not raise) the appropriate PublisherError subclass
    for the caller to raise — keeps this function trivially unit-testable
    without needing to catch-and-rethrow at every call site."""

    if not exc.response_received:
        if is_irreversible:
            return PublisherAmbiguousError(
                f"{operation}: no response received (connection failure/timeout) for an irreversible call",
                partial_platform_state=partial_platform_state,
            )
        return PublisherRetryableError(f"{operation}: no response received (connection failure/timeout)")

    error_body = exc.error_body or {}
    error = error_body.get("error") if isinstance(error_body, dict) else None
    if not isinstance(error, dict):
        if is_irreversible:
            return PublisherAmbiguousError(
                f"{operation}: response received but body could not be interpreted as a Meta error",
                partial_platform_state=partial_platform_state,
            )
        return PublisherRetryableError(f"{operation}: response received but body could not be interpreted")

    code = error.get("code")
    subcode = error.get("error_subcode")
    safe_message = "Instagram could not complete this request right now."

    if code in _RETRYABLE_CODES:
        failure = PublisherFailure(
            category=FailureCategory.RATE_LIMITED if code in (4, 17, 341) else FailureCategory.TRANSIENT_PLATFORM_ERROR,
            retryable=True, operation=operation, safe_message=safe_message, occurred_at=_now_iso(),
            platform_code=code, platform_subcode=subcode, retry_after_seconds=_retry_after_seconds(error_body),
        )
        return PublisherRetryableError(f"{operation}: retryable Meta error code={code}", failure=failure)

    if code in _PERMANENT_CODES:
        category = FailureCategory.INVALID_CREDENTIALS if code == 190 else (
            FailureCategory.MISSING_PERMISSION if code == 10 else FailureCategory.CONTAINER_REJECTED
        )
        failure = PublisherFailure(
            category=category, retryable=False, operation=operation, safe_message=safe_message,
            occurred_at=_now_iso(), platform_code=code, platform_subcode=subcode,
        )
        return PublisherPermanentError(f"{operation}: permanent Meta error code={code}", failure=failure)

    if code is not None and 200 <= code <= 299:
        failure = PublisherFailure(
            category=FailureCategory.MISSING_PERMISSION, retryable=False, operation=operation,
            safe_message=safe_message, occurred_at=_now_iso(), platform_code=code, platform_subcode=subcode,
        )
        return PublisherPermanentError(f"{operation}: permission Meta error code={code}", failure=failure)

    # An unrecognized code: conservative default. A read-only/creation
    # operation is treated as retryable-by-elimination (mirrors this
    # codebase's established "specific enumerated = one thing, everything
    # else = retryable" pattern); the one irreversible operation is
    # treated as ambiguous rather than assumed safe to repeat.
    if is_irreversible:
        return PublisherAmbiguousError(
            f"{operation}: unrecognized Meta error code={code} on an irreversible call",
            partial_platform_state=partial_platform_state,
        )
    failure = PublisherFailure(
        category=FailureCategory.UNKNOWN_PLATFORM_ERROR, retryable=True, operation=operation,
        safe_message=safe_message, occurred_at=_now_iso(), platform_code=code, platform_subcode=subcode,
    )
    return PublisherRetryableError(f"{operation}: unrecognized Meta error code={code}", failure=failure)
