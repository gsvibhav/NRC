from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from src.ai.analysis_service import AnalysisService
from src.ai.clarification_service import ClarificationService
from src.ai.content_planning_service import ContentPlanningService
from src.ai.draft_editing_service import DraftEditingService
from src.ai.draft_generation_service import DraftGenerationService
from src.ai.draft_manager import DraftManager
from src.ai.draft_version_manager import DraftVersionManager
from src.config import Config
from src.conversation.manager import ConversationManager
from src.execution.manager import ExecutionManager
from src.execution.service import ExecutionService
from src.main import build_application
from src.publication.manager import PublicationManager
from src.publication.service import PublicationPreparationService
from src.storage.s3_storage import S3Storage
from src.utils.dedup import SeenUpdateTracker
from src.workflow.manager import WorkflowManager

FAKE_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_config(**overrides):
    defaults = dict(
        telegram_bot_token=FAKE_TOKEN,
        allowed_user_ids=frozenset({1, 2}),
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


def test_build_application_registers_expected_commands():
    application = build_application(_make_config())

    handlers = application.handlers[0]
    command_handlers = [h for h in handlers if isinstance(h, CommandHandler)]
    registered_commands = {command for h in command_handlers for command in h.commands}

    assert registered_commands == {
        "start", "help", "status", "cancel", "retry", "instagram_status", "instagram_test_publish",
    }


def test_build_application_registers_three_message_handlers():
    application = build_application(_make_config())

    handlers = application.handlers[0]
    message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]

    # photo/video -> media, plain text -> text_reply, everything else -> unhandled.
    assert len(message_handlers) == 3


def test_build_application_registers_callback_handlers():
    application = build_application(_make_config())

    handlers = application.handlers[0]
    callback_handlers = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    patterns = {h.pattern.pattern for h in callback_handlers}

    assert len(callback_handlers) == 3
    assert patterns == {r"^review:", r"^dispatch:", r"^ilv:"}


def test_build_application_stores_allowed_user_ids_in_bot_data():
    config = _make_config(allowed_user_ids=frozenset({7, 8}))

    application = build_application(config)

    assert application.bot_data["allowed_user_ids"] == frozenset({7, 8})


def test_build_application_stores_config_in_bot_data():
    config = _make_config()

    application = build_application(config)

    assert application.bot_data["config"] is config


def test_build_application_stores_s3_storage_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["storage"], S3Storage)


def test_build_application_stores_seen_updates_tracker_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["seen_updates"], SeenUpdateTracker)


def test_build_application_stores_workflow_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["workflow_manager"], WorkflowManager)


def test_build_application_stores_conversation_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["conversation_manager"], ConversationManager)


def test_build_application_stores_analysis_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["analysis_service"], AnalysisService)


def test_build_application_stores_clarification_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["clarification_service"], ClarificationService)


def test_build_application_stores_content_planning_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["content_planning_service"], ContentPlanningService)


def test_build_application_stores_draft_generation_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["draft_generation_service"], DraftGenerationService)


def test_build_application_stores_draft_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["draft_manager"], DraftManager)


def test_build_application_stores_draft_version_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["draft_version_manager"], DraftVersionManager)


def test_build_application_stores_draft_editing_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["draft_editing_service"], DraftEditingService)


def test_build_application_stores_publication_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["publication_manager"], PublicationManager)


def test_build_application_stores_publication_preparation_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["publication_preparation_service"], PublicationPreparationService)


def test_build_application_stores_execution_manager_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["execution_manager"], ExecutionManager)


def test_build_application_stores_execution_service_in_bot_data():
    application = build_application(_make_config())

    assert isinstance(application.bot_data["execution_service"], ExecutionService)


def test_build_application_stores_instagram_diagnostics_service_in_bot_data():
    from src.publisher.instagram.diagnostics import InstagramDiagnosticsService

    application = build_application(_make_config())

    assert isinstance(application.bot_data["instagram_diagnostics_service"], InstagramDiagnosticsService)


def test_build_application_stores_live_validation_service_in_bot_data():
    from src.execution.live_validation import ControlledLiveValidationService

    application = build_application(_make_config())

    assert isinstance(application.bot_data["live_validation_service"], ControlledLiveValidationService)
