from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Config
from src.conversation.errors import ConversationPersistenceError
from src.media import ingestion
from src.media.errors import ConversationRecordError, StorageUploadError, WorkflowRecordError
from src.media.ingestion import (
    MediaIngestionResult,
    _build_media_record,
    _build_s3_metadata,
    ingest_media,
)
from src.media.types import MediaType, TelegramMediaInfo
from src.storage.s3_storage import S3UploadError
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

FAKE_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
ONE_MB = 1024 * 1024


def _make_config(**overrides):
    defaults = dict(
        telegram_bot_token=FAKE_TOKEN,
        allowed_user_ids=frozenset({1}),
        environment="development",
        log_level="INFO",
        aws_access_key_id="fake-access-key",
        aws_secret_access_key="fake-secret-key",
        aws_region="us-east-1",
        s3_bucket_name="fake-bucket",
        s3_key_prefix="media",
        max_image_size_mb=20,
        max_video_size_mb=20,
        anthropic_api_key="fake-anthropic-key",
        anthropic_model="claude-opus-5",
        anthropic_max_tokens=4096,
        anthropic_request_timeout_seconds=60,
        anthropic_max_retries=2,
        analysis_schema_version=1,
        max_clarification_questions=4,
        max_planned_outputs=3,
        content_plan_schema_version=1,
        draft_schema_version=1,
        max_instagram_caption_length=2200,
        max_hashtags=5,
        publication_schema_version=1,
        instagram_publishing_enabled=False,
        meta_graph_api_version="v25.0",
        instagram_account_id=None,
        meta_access_token=None,
        instagram_media_url_ttl_seconds=1800,
        instagram_container_poll_interval_seconds=5.0,
        instagram_container_poll_timeout_seconds=120.0,
        dispatch_lease_seconds=300,
        meta_request_timeout_seconds=30.0,
        instagram_live_test_operator_ids=frozenset(),
    )
    defaults.update(overrides)
    return Config(**defaults)


def _make_photo_message(file_size=1 * ONE_MB):
    photo_size = MagicMock()
    photo_size.file_id = "photo-file-id"
    photo_size.file_unique_id = "photo-unique-id"
    photo_size.file_size = file_size

    message = MagicMock()
    message.photo = [photo_size]
    message.video = None
    return message


def _make_workflow_manager():
    # Mirrors the real WorkflowManager.create_workflow's contract: the
    # returned document's workflow_id is always exactly what was passed in
    # (ingest_media generates it before calling create_workflow and relies
    # on that echo — see src/media/ingestion.py).
    manager = MagicMock()

    def _create_workflow(*, workflow_id, telegram_user_id, media, **_kwargs):
        return WorkflowDocument(
            workflow_id=workflow_id,
            telegram_user_id=telegram_user_id,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            state=WorkflowState.ANALYZING_MEDIA,
            media=media,
        )

    manager.create_workflow.side_effect = _create_workflow
    return manager


def _make_conversation_manager():
    return MagicMock()


async def test_ingest_media_success_path_uploads_creates_workflow_and_cleans_up(monkeypatch):
    message = _make_photo_message()
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    workflow_manager = _make_workflow_manager()
    conversation_manager = _make_conversation_manager()

    download_mock = AsyncMock(return_value="/tmp/fake-temp-path.jpg")
    cleanup_mock = MagicMock()
    monkeypatch.setattr(ingestion, "download_to_temp_file", download_mock)
    monkeypatch.setattr(ingestion, "cleanup_temp_file", cleanup_mock)

    result = await ingest_media(
        message, bot, telegram_user_id=42, config=config, storage=storage,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    assert isinstance(result, MediaIngestionResult)
    assert result.media_type is MediaType.PHOTO

    download_mock.assert_awaited_once_with(bot, "photo-file-id", MediaType.PHOTO)
    storage.upload_file.assert_called_once()
    args, kwargs = storage.upload_file.call_args
    assert args[0] == "/tmp/fake-temp-path.jpg"
    assert kwargs["content_type"] == "image/jpeg"

    cleanup_mock.assert_called_once_with("/tmp/fake-temp-path.jpg")

    workflow_manager.create_workflow.assert_called_once()
    _, create_kwargs = workflow_manager.create_workflow.call_args
    assert create_kwargs["telegram_user_id"] == 42
    assert create_kwargs["media"]["telegram_file_id"] == "photo-file-id"

    conversation_manager.set_active_workflow.assert_called_once_with(42, result.workflow_id)


async def test_ingest_media_uses_storage_generated_key(monkeypatch):
    message = _make_photo_message()
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    storage.build_object_key.return_value = "media/42/some-id/original/photo-unique-id.jpg"
    workflow_manager = _make_workflow_manager()
    conversation_manager = _make_conversation_manager()

    monkeypatch.setattr(
        ingestion, "download_to_temp_file", AsyncMock(return_value="/tmp/path.jpg")
    )
    monkeypatch.setattr(ingestion, "cleanup_temp_file", MagicMock())

    await ingest_media(
        message, bot, telegram_user_id=42, config=config, storage=storage,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    storage.build_object_key.assert_called_once()
    call_args = storage.build_object_key.call_args.args
    assert call_args[0] == 42  # telegram_user_id
    assert call_args[2] == "photo-unique-id.jpg"  # filename derived from file_unique_id

    upload_args, _ = storage.upload_file.call_args
    assert upload_args[1] == "media/42/some-id/original/photo-unique-id.jpg"

    # The media record handed to the workflow manager should reference the
    # exact same S3 key the upload used.
    _, create_kwargs = workflow_manager.create_workflow.call_args
    assert create_kwargs["media"]["s3_key"] == "media/42/some-id/original/photo-unique-id.jpg"


async def test_ingest_media_propagates_validation_errors_without_downloading(monkeypatch):
    message = _make_photo_message(file_size=999 * ONE_MB)  # far above the default limit
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    workflow_manager = _make_workflow_manager()
    conversation_manager = _make_conversation_manager()

    download_mock = AsyncMock()
    monkeypatch.setattr(ingestion, "download_to_temp_file", download_mock)

    with pytest.raises(Exception):
        await ingest_media(
            message, bot, telegram_user_id=1, config=config, storage=storage,
            workflow_manager=workflow_manager, conversation_manager=conversation_manager,
        )

    download_mock.assert_not_called()
    storage.upload_file.assert_not_called()
    workflow_manager.create_workflow.assert_not_called()


async def test_ingest_media_cleans_up_temp_file_when_upload_fails(monkeypatch):
    message = _make_photo_message()
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    storage.upload_file.side_effect = S3UploadError("upload failed")
    workflow_manager = _make_workflow_manager()
    conversation_manager = _make_conversation_manager()

    monkeypatch.setattr(
        ingestion, "download_to_temp_file", AsyncMock(return_value="/tmp/fake-temp-path.jpg")
    )
    cleanup_mock = MagicMock()
    monkeypatch.setattr(ingestion, "cleanup_temp_file", cleanup_mock)

    with pytest.raises(StorageUploadError):
        await ingest_media(
            message, bot, telegram_user_id=1, config=config, storage=storage,
            workflow_manager=workflow_manager, conversation_manager=conversation_manager,
        )

    cleanup_mock.assert_called_once_with("/tmp/fake-temp-path.jpg")
    workflow_manager.create_workflow.assert_not_called()


async def test_ingest_media_raises_workflow_record_error_when_workflow_creation_fails(monkeypatch):
    message = _make_photo_message()
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    workflow_manager = MagicMock()
    workflow_manager.create_workflow.side_effect = WorkflowPersistenceError("s3 down")
    conversation_manager = _make_conversation_manager()

    monkeypatch.setattr(
        ingestion, "download_to_temp_file", AsyncMock(return_value="/tmp/fake-temp-path.jpg")
    )
    monkeypatch.setattr(ingestion, "cleanup_temp_file", MagicMock())

    with pytest.raises(WorkflowRecordError):
        await ingest_media(
            message, bot, telegram_user_id=1, config=config, storage=storage,
            workflow_manager=workflow_manager, conversation_manager=conversation_manager,
        )

    # The upload itself already succeeded (storage.upload_file was called);
    # only the workflow record failed.
    storage.upload_file.assert_called_once()
    conversation_manager.set_active_workflow.assert_not_called()


async def test_ingest_media_raises_conversation_record_error_when_pointer_set_fails(monkeypatch):
    message = _make_photo_message()
    bot = MagicMock()
    config = _make_config()
    storage = MagicMock()
    workflow_manager = _make_workflow_manager()
    conversation_manager = MagicMock()
    conversation_manager.set_active_workflow.side_effect = ConversationPersistenceError("s3 down")

    monkeypatch.setattr(
        ingestion, "download_to_temp_file", AsyncMock(return_value="/tmp/fake-temp-path.jpg")
    )
    monkeypatch.setattr(ingestion, "cleanup_temp_file", MagicMock())

    with pytest.raises(ConversationRecordError):
        await ingest_media(
            message, bot, telegram_user_id=1, config=config, storage=storage,
            workflow_manager=workflow_manager, conversation_manager=conversation_manager,
        )

    # The workflow record was already created; only the pointer failed. Both
    # calls must reference the exact same workflow_id.
    workflow_manager.create_workflow.assert_called_once()
    created_workflow_id = workflow_manager.create_workflow.call_args.kwargs["workflow_id"]
    conversation_manager.set_active_workflow.assert_called_once_with(1, created_workflow_id)


def test_build_s3_metadata_includes_expected_fields_and_omits_missing_filename():
    info = TelegramMediaInfo(
        media_type=MediaType.PHOTO,
        file_id="file-id",
        file_unique_id="unique-id",
        file_size=123,
        mime_type="image/jpeg",
        file_name=None,
    )

    metadata = _build_s3_metadata(info, telegram_user_id=7, workflow_id="wf-1")

    assert metadata["telegram-file-id"] == "file-id"
    assert metadata["telegram-file-unique-id"] == "unique-id"
    assert metadata["telegram-user-id"] == "7"
    assert metadata["media-type"] == "photo"
    assert metadata["mime-type"] == "image/jpeg"
    assert metadata["workflow-id"] == "wf-1"
    assert "uploaded-at" in metadata
    assert "original-filename" not in metadata


def test_build_s3_metadata_includes_filename_when_available():
    info = TelegramMediaInfo(
        media_type=MediaType.VIDEO,
        file_id="file-id",
        file_unique_id="unique-id",
        file_size=123,
        mime_type="video/mp4",
        file_name="clip.mp4",
    )

    metadata = _build_s3_metadata(info, telegram_user_id=7, workflow_id="wf-1")

    assert metadata["original-filename"] == "clip.mp4"


def test_build_media_record_includes_expected_fields():
    info = TelegramMediaInfo(
        media_type=MediaType.VIDEO,
        file_id="file-id",
        file_unique_id="unique-id",
        file_size=456,
        mime_type="video/mp4",
        file_name="clip.mp4",
    )

    record = _build_media_record(info, s3_key="media/1/wf-1/original/unique-id.mp4")

    assert record["telegram_file_id"] == "file-id"
    assert record["telegram_file_unique_id"] == "unique-id"
    assert record["media_type"] == "video"
    assert record["mime_type"] == "video/mp4"
    assert record["file_name"] == "clip.mp4"
    assert record["file_size"] == 456
    assert record["s3_key"] == "media/1/wf-1/original/unique-id.mp4"
    assert "uploaded_at" in record
