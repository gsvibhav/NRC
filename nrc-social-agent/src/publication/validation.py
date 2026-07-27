"""Business-rule validation for a freshly-built publication package,
run before it is ever persisted (see service.py). Contract minimality
itself is enforced structurally — `PublicationContent`/`MediaAssetReference`/
`PublicationPackage` (models.py) simply have no field capable of holding a
full workflow, full analysis, clarification history, a content-plan body,
draft-version history, a raw Claude response, a prompt, conversation
messages, a Telegram callback, or AWS credentials — so no runtime scan is
needed to keep those out of `content`/`media`/`approval`. The one
genuinely open field, `metadata`, is defensively scanned here anyway
against a denylist of banned key names, as a backstop.

Two families, mirroring draft_validation.py's identical split:
1. **Structural rules** — non-empty caption, length limits, hashtag
   format, media cardinality/duplication. Exact and reliable.
2. **Contract-minimality backstop** — the `metadata` key-name denylist.
   Necessarily a pattern check, not a semantic guarantee — see this
   module's docstring in README.md's "Known limitations".
"""

from __future__ import annotations

import re

from .errors import PublicationValidationFailedError
from .models import MediaAssetReference, PublicationContent

_HASHTAG_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

MIN_MEANINGFUL_CAPTION_LENGTH = 20

_BANNED_METADATA_KEYS = frozenset(
    {
        "workflow_document", "workflow", "analysis", "clarification_history", "clarification_context",
        "content_plan", "plan", "draft_history", "claude_prompt", "prompt", "raw_response",
        "telegram_update", "telegram_update_id", "conversation", "aws_access_key_id",
        "aws_secret_access_key", "api_key", "access_token", "refresh_token",
    }
)


def validate_publication_package(
    content: PublicationContent,
    media: list[MediaAssetReference],
    *,
    max_caption_length: int,
    max_hashtags: int,
    metadata: dict | None = None,
) -> None:
    """Raises PublicationValidationFailedError with a specific reason on
    any rule violation. Returns None (silently) if the package passes."""

    caption = content.caption.strip()
    if len(caption) < MIN_MEANINGFUL_CAPTION_LENGTH:
        raise PublicationValidationFailedError(
            f"caption is only {len(caption)} characters — below the minimum meaningful length "
            f"({MIN_MEANINGFUL_CAPTION_LENGTH})"
        )

    combined_length = len(caption) + (len(content.cta) if content.cta else 0) + sum(len(h) for h in content.hashtags)
    if combined_length > max_caption_length:
        raise PublicationValidationFailedError(
            f"combined caption+CTA+hashtags length ({combined_length}) exceeds the configured "
            f"maximum of {max_caption_length}"
        )

    if len(content.hashtags) > max_hashtags:
        raise PublicationValidationFailedError(
            f"package has {len(content.hashtags)} hashtags, exceeding the configured maximum of {max_hashtags}"
        )
    for hashtag in content.hashtags:
        if not _HASHTAG_PATTERN.match(hashtag):
            raise PublicationValidationFailedError(f"malformed hashtag: {hashtag!r}")

    if content.cta and content.cta.strip().lower() in caption.lower():
        raise PublicationValidationFailedError("CTA is duplicated verbatim inside the caption")

    if not media:
        raise PublicationValidationFailedError("package has no media references")
    if len(media) != 1:
        # V1 cardinality: exactly one asset, matching what ingestion ever
        # stores per workflow — see builder.py's module docstring.
        raise PublicationValidationFailedError(f"expected exactly one media reference, got {len(media)}")

    asset_ids = [asset.asset_id for asset in media]
    if len(asset_ids) != len(set(asset_ids)):
        raise PublicationValidationFailedError(f"duplicate media asset_id(s): {asset_ids!r}")

    for asset in media:
        storage_reference = asset.storage_reference
        if not storage_reference.get("bucket") or not storage_reference.get("key"):
            raise PublicationValidationFailedError(f"media asset {asset.asset_id!r} has an incomplete storage reference")
        if storage_reference["key"].startswith("http://") or storage_reference["key"].startswith("https://"):
            raise PublicationValidationFailedError(
                f"media asset {asset.asset_id!r} storage reference looks like a URL, not a durable S3 key"
            )

    for key in (metadata or {}):
        if key.lower() in _BANNED_METADATA_KEYS:
            raise PublicationValidationFailedError(f"metadata contains a banned key: {key!r}")
