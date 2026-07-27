"""Shared data shapes for media ingestion.

Per docs/WORKFLOW.md, Version 1 accepts exactly two Telegram media kinds:
photos and videos (never generic "document" uploads).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"


# Telegram always delivers "photo"-type messages as JPEG, and "video"-type
# messages as MP4 — these are the only formats reachable through those two
# message types (anything else arrives as a "document", which is out of
# scope). See docs/WORKFLOW.md for accepted-format documentation.
ACCEPTED_MIME_TYPES = {
    MediaType.PHOTO: {"image/jpeg"},
    MediaType.VIDEO: {"video/mp4"},
}

DEFAULT_EXTENSION = {
    MediaType.PHOTO: "jpg",
    MediaType.VIDEO: "mp4",
}


@dataclass(frozen=True)
class TelegramMediaInfo:
    """Metadata extracted from a Telegram Update, before any download."""

    media_type: MediaType
    file_id: str
    file_unique_id: str
    file_size: int | None
    mime_type: str | None
    file_name: str | None
