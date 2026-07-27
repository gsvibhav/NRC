"""Orchestrates one media upload: receive → validate → download → store →
create workflow record → set active workflow pointer.

Implements the docs/WORKFLOW.md `RECEIVING_MEDIA` → `UPLOADING_MEDIA` →
(success, or `FAILED`) path, persists the resulting workflow record via the
Workflow Manager immediately after a successful upload (landing it in
`ANALYZING_MEDIA` — see src/workflow/manager.py for why), and — since
Milestone 3.5 — points that user's conversation at the new workflow via the
Conversation Manager, so future commands (and, eventually, replies) can
find it without the user supplying the workflow_id themselves. No further
processing happens: there is still no Claude analysis, no follow-up
questions, and nothing beyond those two persisted records.

`workflow_id` is generated here, before the upload, rather than inside the
Workflow Manager — the frozen S3 media layout (media/<user>/<workflow_id>/
original/<file>, see README.md) embeds it in the upload's own key, so it
has to exist before the upload happens, not after.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from telegram import Bot, Message

from ..config import Config
from ..conversation.errors import ConversationError
from ..conversation.manager import ConversationManager
from ..storage.s3_storage import S3ObjectCollisionError, S3Storage, S3UploadError
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from .download import cleanup_temp_file, download_to_temp_file
from .errors import ConversationRecordError, StorageUploadError, WorkflowRecordError
from .types import DEFAULT_EXTENSION, MediaType, TelegramMediaInfo
from .validation import extract_media_info, validate_media

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaIngestionResult:
    workflow_id: str
    media_type: MediaType


async def ingest_media(
    message: Message,
    bot: Bot,
    telegram_user_id: int,
    config: Config,
    storage: S3Storage,
    workflow_manager: WorkflowManager,
    conversation_manager: ConversationManager,
) -> MediaIngestionResult:
    """Run the full ingestion pipeline for one Telegram message's media.

    Raises a MediaIngestionError subclass (see errors.py) on any expected
    failure; each carries a short, generic `user_message`. Never returns a
    result unless the S3 upload, the workflow record, and the conversation's
    active-workflow pointer all succeeded.
    """

    workflow_id = uuid.uuid4().hex

    info = extract_media_info(message)
    logger.info(
        "Media received user_id=%s media_type=%s workflow_id=%s",
        telegram_user_id,
        info.media_type.value,
        workflow_id,
    )

    validate_media(
        info,
        max_image_size_bytes=config.max_image_size_bytes,
        max_video_size_bytes=config.max_video_size_bytes,
    )
    logger.info(
        "Media validation accepted user_id=%s workflow_id=%s file_unique_id=%s",
        telegram_user_id,
        workflow_id,
        info.file_unique_id,
    )

    temp_path: str | None = None
    key: str | None = None
    try:
        logger.info("Telegram download started user_id=%s workflow_id=%s", telegram_user_id, workflow_id)
        temp_path = await download_to_temp_file(bot, info.file_id, info.media_type)
        logger.info("Telegram download completed user_id=%s workflow_id=%s", telegram_user_id, workflow_id)

        filename = f"{info.file_unique_id}.{DEFAULT_EXTENSION[info.media_type]}"
        key = storage.build_object_key(telegram_user_id, workflow_id, filename)
        s3_metadata = _build_s3_metadata(info, telegram_user_id, workflow_id)

        logger.info("S3 upload started user_id=%s workflow_id=%s", telegram_user_id, workflow_id)
        storage.upload_file(temp_path, key, content_type=info.mime_type, metadata=s3_metadata)
        logger.info("S3 upload completed user_id=%s workflow_id=%s", telegram_user_id, workflow_id)
    except (S3UploadError, S3ObjectCollisionError) as exc:
        logger.error(
            "Upload failed user_id=%s workflow_id=%s error=%s", telegram_user_id, workflow_id, exc
        )
        raise StorageUploadError(str(exc)) from exc
    finally:
        if temp_path is not None:
            cleanup_temp_file(temp_path)
            logger.info(
                "Temporary file cleaned up user_id=%s workflow_id=%s", telegram_user_id, workflow_id
            )

    media_record = _build_media_record(info, key)

    try:
        document = workflow_manager.create_workflow(
            workflow_id=workflow_id, telegram_user_id=telegram_user_id, media=media_record
        )
    except WorkflowError as exc:
        logger.error(
            "Workflow record creation failed user_id=%s workflow_id=%s error=%s",
            telegram_user_id,
            workflow_id,
            exc,
        )
        raise WorkflowRecordError(str(exc)) from exc

    try:
        conversation_manager.set_active_workflow(telegram_user_id, workflow_id)
    except ConversationError as exc:
        logger.error(
            "Setting active workflow pointer failed user_id=%s workflow_id=%s error=%s",
            telegram_user_id,
            workflow_id,
            exc,
        )
        raise ConversationRecordError(str(exc)) from exc

    return MediaIngestionResult(workflow_id=document.workflow_id, media_type=info.media_type)


def _build_s3_metadata(info: TelegramMediaInfo, telegram_user_id: int, workflow_id: str) -> dict[str, str]:
    metadata = {
        "telegram-file-id": info.file_id,
        "telegram-file-unique-id": info.file_unique_id,
        "telegram-user-id": str(telegram_user_id),
        "media-type": info.media_type.value,
        "mime-type": info.mime_type or "",
        "uploaded-at": datetime.now(timezone.utc).isoformat(),
        "workflow-id": workflow_id,
    }
    if info.file_name:
        metadata["original-filename"] = info.file_name
    return metadata


def _build_media_record(info: TelegramMediaInfo, s3_key: str) -> dict:
    return {
        "telegram_file_id": info.file_id,
        "telegram_file_unique_id": info.file_unique_id,
        "media_type": info.media_type.value,
        "mime_type": info.mime_type,
        "file_name": info.file_name,
        "file_size": info.file_size,
        "s3_key": s3_key,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
