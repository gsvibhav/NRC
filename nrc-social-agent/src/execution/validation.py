"""Business-rule validation for execution documents, run before they are
ever persisted. Contract minimality is enforced structurally first —
`ExecutionDocument` (models.py) simply has no field capable of holding a
caption, a hashtag, a prompt, a Claude response, a Telegram callback, an
access token, or a raw platform response — so no runtime scan is needed
to keep those out of the document's own named fields. The genuinely open
dict fields (`metadata`, and — Milestone 10 — `platform_state`, `result`,
`failure`, `dispatch_authorization`) are defensively scanned here anyway
against a denylist of banned key names, as a backstop, mirroring
src/publication/validation.py's identical pattern.
"""

from __future__ import annotations

from .errors import ExecutionValidationFailedError
from .models import ExecutionDocument, ExecutionStatus

_BANNED_METADATA_KEYS = frozenset(
    {
        "caption", "hashtags", "cta", "workflow_document", "workflow", "analysis",
        "clarification_history", "clarification_context", "content_plan", "plan",
        "draft_history", "claude_prompt", "prompt", "raw_response", "platform_response",
        "instagram_post_id", "telegram_update", "telegram_update_id", "conversation",
        "aws_access_key_id", "aws_secret_access_key", "api_key", "access_token", "refresh_token",
        "authorization", "authorization_header", "media_url", "presigned_url", "source_url",
        "url", "video_url", "image_url", "raw_error", "raw_error_body", "raw_headers", "stack_trace",
    }
)


def validate_execution(document: ExecutionDocument, *, metadata: dict | None = None) -> None:
    """Raises ExecutionValidationFailedError with a specific reason on any
    rule violation. Returns None (silently) if the document passes.

    Only `READY_FOR_DISPATCH` is ever constructed by this milestone's
    ExecutionManager.create_execution() — the status/attempt checks below
    are still enforced independently here (rather than only trusted at
    construction time) so a future milestone that *does* start
    transitioning execution status can reuse this same check unchanged."""

    if document.status is not ExecutionStatus.READY_FOR_DISPATCH:
        raise ExecutionValidationFailedError(
            f"unexpected execution status for this milestone: {document.status.value!r} "
            f"(only READY_FOR_DISPATCH is reachable)"
        )

    if document.attempt != 0:
        raise ExecutionValidationFailedError(
            f"unexpected attempt count for a freshly-created execution: {document.attempt!r} (expected 0)"
        )

    if not document.publisher:
        raise ExecutionValidationFailedError("execution document has no publisher")

    for key in (metadata if metadata is not None else document.metadata):
        if key.lower() in _BANNED_METADATA_KEYS:
            raise ExecutionValidationFailedError(f"metadata contains a banned key: {key!r}")


def validate_dispatch_dict(field_name: str, value: dict | None) -> None:
    """Milestone 10: the same banned-key backstop as validate_execution()'s
    metadata scan, applied to `platform_state`/`result`/`failure`/
    `dispatch_authorization` before the dispatch service ever asks the
    manager to persist them. Called once per field, right before each
    dispatch-lifecycle write — a secret or a URL making it into one of
    these dicts is a bug in the publisher adapter, and this is the last
    structural line of defense against it reaching S3."""

    for key in value or {}:
        if key.lower() in _BANNED_METADATA_KEYS:
            raise ExecutionValidationFailedError(f"{field_name} contains a banned key: {key!r}")
