"""Media extraction and validation.

Per docs/WORKFLOW.md §7.1 and the Milestone 2 brief: validate before any
download or storage write, and never trust a filename extension alone.

We deliberately don't use filename extensions as a validation input at all.
Telegram routes "photo" and "video" through distinct, structured message
fields (`message.photo`, `message.video`) that a client can't spoof via a
filename, and Telegram itself reports `mime_type` for videos. Those two
signals — which we can't influence from outside Telegram — are the sole
basis for type/format decisions here.
"""

from __future__ import annotations

from telegram import Message

from .errors import (
    MediaTooLargeError,
    MissingTelegramFileError,
    NoMediaError,
    UnknownMediaSizeError,
    UnsupportedMediaTypeError,
)
from .types import ACCEPTED_MIME_TYPES, MediaType, TelegramMediaInfo


def extract_media_info(message: Message) -> TelegramMediaInfo:
    """Pull media metadata out of a Telegram message. Raises NoMediaError if
    the message contains neither a photo nor a video."""

    if message.photo:
        # PhotoSize entries are ordered smallest to largest; the largest is
        # the original resolution Telegram stored for this photo.
        largest = message.photo[-1]
        return TelegramMediaInfo(
            media_type=MediaType.PHOTO,
            file_id=largest.file_id,
            file_unique_id=largest.file_unique_id,
            file_size=largest.file_size,
            # Telegram always compresses "photo"-type messages to JPEG; the
            # PhotoSize object itself carries no mime_type field.
            mime_type="image/jpeg",
            file_name=None,
        )

    if message.video:
        video = message.video
        return TelegramMediaInfo(
            media_type=MediaType.VIDEO,
            file_id=video.file_id,
            file_unique_id=video.file_unique_id,
            file_size=video.file_size,
            mime_type=video.mime_type,
            file_name=video.file_name,
        )

    raise NoMediaError("message contains neither a photo nor a video")


def validate_media(info: TelegramMediaInfo, *, max_image_size_bytes: int, max_video_size_bytes: int) -> None:
    """Raise a specific MediaIngestionError if `info` isn't acceptable."""

    if not info.file_id or not info.file_unique_id:
        raise MissingTelegramFileError(
            f"missing file identifiers for media_type={info.media_type.value}"
        )

    accepted_mime_types = ACCEPTED_MIME_TYPES[info.media_type]
    if info.mime_type is None or info.mime_type not in accepted_mime_types:
        raise UnsupportedMediaTypeError(
            f"unsupported mime_type={info.mime_type!r} for media_type={info.media_type.value}"
        )

    if info.file_size is None:
        raise UnknownMediaSizeError(f"missing file_size for media_type={info.media_type.value}")

    max_bytes = max_image_size_bytes if info.media_type is MediaType.PHOTO else max_video_size_bytes
    if info.file_size > max_bytes:
        raise MediaTooLargeError(
            f"file_size={info.file_size} exceeds max={max_bytes} bytes "
            f"for media_type={info.media_type.value}"
        )
