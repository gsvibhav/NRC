"""Streams a validated Telegram file to a bounded local temp file.

python-telegram-bot has no built-in way to stream a Telegram file straight
into S3 — `Bot.get_file()` / `File.download_to_drive()` write to a local
path, streaming in chunks internally (never loading the whole file into a
Python bytes object). We use exactly that: download to a single-purpose
temporary file on local disk, upload *that* file to S3 (boto3's
`upload_file` also streams from disk in chunks), then always delete the
temp file — success or failure — so nothing lingers as a long-lived local
copy. This keeps peak memory bounded regardless of media size, at the cost
of a brief local disk footprint per upload, which is unavoidable without a
direct Telegram-to-S3 streaming path.
"""

from __future__ import annotations

import logging
import os
import tempfile

from telegram import Bot
from telegram.error import TelegramError

from .errors import TelegramDownloadError
from .types import DEFAULT_EXTENSION, MediaType

logger = logging.getLogger(__name__)


async def download_to_temp_file(bot: Bot, file_id: str, media_type: MediaType) -> str:
    """Download `file_id` to a new bounded temp file and return its path.

    Raises TelegramDownloadError if Telegram can't provide or serve the file.
    The caller is responsible for deleting the returned path via
    `cleanup_temp_file`, in every success and failure path.
    """

    suffix = f".{DEFAULT_EXTENSION[media_type]}"
    fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="nrc-social-agent-")
    os.close(fd)  # We only need the reserved, unique path; PTB opens it itself.

    try:
        telegram_file = await bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=temp_path)
    except TelegramError as exc:
        cleanup_temp_file(temp_path)
        raise TelegramDownloadError(f"failed to download file_id={file_id}: {exc}") from exc
    except Exception:
        cleanup_temp_file(temp_path)
        raise

    return temp_path


def cleanup_temp_file(path: str) -> None:
    """Best-effort temp-file deletion. Never raises — a cleanup failure must
    not mask (or be mistaken for) the original ingestion result."""

    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Failed to clean up temporary file: %s", path, exc_info=True)
