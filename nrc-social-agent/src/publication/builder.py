"""Pure, I/O-free mapping from an approved draft's content and a
workflow's media record into the publication domain's own shapes.

Both functions here perform **mechanical transformations only** — never a
semantic rewrite, never a Claude call, never brand-voice reapplication.
Permitted: trimming accidental outer whitespace, normalizing hashtag
representation (stripping a redundant leading `#` so the package always
stores bare tag text), and converting to this domain's own dataclasses.
If the future platform needs different formatting, that belongs in a
publisher adapter or a deliberately versioned channel formatter — not
here, and not an AI rewrite (see README.md's "Content mapping").
"""

from __future__ import annotations

from .errors import MissingMediaForPublicationError, UnsupportedPublicationMediaTypeError
from .models import MediaAssetReference, PublicationChannel, PublicationContent

# V1 only ever ingests one of these two MIME types per workflow (see
# src/media/types.py's ACCEPTED_MIME_TYPES) — and, per src/ai/analysis_service.py's
# own gate, only a photo (`image/jpeg`) can currently reach an approved
# draft at all (video analysis isn't supported yet, see README.md's
# "Known limitations" — a pre-existing scope tension this milestone makes
# newly visible at the publication boundary, not one it introduces).
# `video/mp4` is allowed here anyway, ahead of that gate lifting, so this
# mapping doesn't need to change the day it does.
_SUPPORTED_MEDIA_TYPES_BY_CHANNEL = {
    PublicationChannel.INSTAGRAM: frozenset({"image/jpeg", "video/mp4"}),
}


def build_publication_content(draft_content: dict) -> PublicationContent:
    """Maps the approved draft's `content` dict into a `PublicationContent`
    — caption/hashtags/CTA unchanged in substance, only mechanically
    normalized (whitespace, hashtag `#` prefix). Never adds, removes, or
    rewrites wording."""

    caption = (draft_content.get("caption") or "").strip()
    hashtags = [tag.strip().lstrip("#") for tag in (draft_content.get("hashtags") or [])]
    cta = draft_content.get("cta")
    if isinstance(cta, str):
        cta = cta.strip() or None
    return PublicationContent(caption=caption, hashtags=hashtags, cta=cta)


def build_media_references(
    workflow_media: dict, *, channel: PublicationChannel, bucket_name: str
) -> list[MediaAssetReference]:
    """Maps the workflow's single media record into exactly one
    `MediaAssetReference` — V1 ingestion never stores more than one media
    object per workflow (see src/media/ingestion.py), so this never
    invents multi-asset publishing. Raises MissingMediaForPublicationError
    if the workflow has no usable media record at all, or
    UnsupportedPublicationMediaTypeError if its type isn't one this
    channel currently supports. Never copies media bytes, never includes
    a temporary Telegram URL — only the durable `{bucket, key}` S3
    reference already recorded at upload time."""

    if not workflow_media:
        raise MissingMediaForPublicationError("workflow has no media record")

    s3_key = workflow_media.get("s3_key")
    if not s3_key:
        raise MissingMediaForPublicationError("workflow media record has no durable S3 key")

    mime_type = workflow_media.get("mime_type")
    supported = _SUPPORTED_MEDIA_TYPES_BY_CHANNEL.get(channel, frozenset())
    if mime_type not in supported:
        raise UnsupportedPublicationMediaTypeError(
            f"media_type={mime_type!r} is not supported for channel={channel.value!r}"
        )

    asset_id = workflow_media.get("telegram_file_unique_id") or s3_key
    reference = MediaAssetReference(
        asset_id=asset_id,
        media_type=mime_type,
        storage_reference={"bucket": bucket_name, "key": s3_key},
    )
    return [reference]
