"""Application entry point for NRC Social Agent.

Wires configuration, logging, access control, and command handlers together
and runs the Telegram bot via long polling. Run with: python -m src.main
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from .ai.analysis_manager import AnalysisManager
from .ai.analysis_repository import AnalysisRepository
from .ai.analysis_service import AnalysisService
from .ai.analysis_store import AnalysisStore
from .ai.clarification_service import ClarificationService
from .ai.client import ClaudeClient
from .ai.content_plan_manager import ContentPlanManager
from .ai.content_plan_repository import ContentPlanRepository
from .ai.content_plan_store import ContentPlanStore
from .ai.content_planning_service import ContentPlanningService
from .ai.draft_editing_service import DraftEditingService
from .ai.draft_generation_service import DraftGenerationService
from .ai.draft_manager import DraftManager
from .ai.draft_repository import DraftRepository
from .ai.draft_version_manager import DraftVersionManager
from .ai.draft_version_repository import DraftVersionRepository
from .config import Config, ConfigError
from .conversation.manager import ConversationManager
from .conversation.repository import ConversationRepository
from .conversation.store import ConversationStore
from .execution.clock import AsyncioSleeper, SystemClock
from .execution.dispatch_service import ExecutionDispatchService
from .execution.live_validation import ControlledLiveValidationService
from .execution.manager import ExecutionManager
from .execution.repository import ExecutionRepository
from .execution.service import ExecutionService
from .execution.store import ExecutionStore
from .handlers import (
    cancel,
    dispatch_action,
    help_command,
    instagram_live_validation_action,
    instagram_status,
    instagram_test_publish,
    media,
    retry,
    review_action,
    start,
    status,
    text_reply,
    unhandled,
)
from .logging_config import configure_logging
from .publication.manager import PublicationManager
from .publication.repository import PublicationRepository
from .publication.service import PublicationPreparationService
from .publication.store import PublicationStore
from .publisher.instagram.client import HttpxMetaHttpClient
from .publisher.instagram.diagnostics import InstagramDiagnosticsService
from .publisher.instagram.media_access import S3PresignedMediaAccess
from .publisher.instagram.publisher import InstagramPublisher
from .publisher.registry import PublisherRegistry
from .storage.client import build_s3_client
from .storage.s3_storage import S3Storage
from .utils.dedup import SeenUpdateTracker
from .workflow.draft_store import DraftStore
from .workflow.manager import WorkflowManager
from .workflow.repository import WorkflowRepository
from .workflow.state_store import WorkflowStateStore

logger = logging.getLogger(__name__)


def build_application(config: Config) -> Application:
    application = Application.builder().token(config.telegram_bot_token).build()

    s3_client = build_s3_client(config)

    storage = S3Storage(
        client=s3_client,
        bucket_name=config.s3_bucket_name,
        key_prefix=config.s3_key_prefix,
    )
    workflow_manager = WorkflowManager(
        WorkflowRepository(WorkflowStateStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    conversation_manager = ConversationManager(
        ConversationRepository(ConversationStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    analysis_manager = AnalysisManager(
        AnalysisRepository(AnalysisStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    content_plan_manager = ContentPlanManager(
        ContentPlanRepository(ContentPlanStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    draft_manager = DraftManager(
        DraftRepository(DraftStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    draft_version_manager = DraftVersionManager(
        DraftVersionRepository(DraftStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    publication_manager = PublicationManager(
        PublicationRepository(PublicationStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    execution_manager = ExecutionManager(
        ExecutionRepository(ExecutionStore(client=s3_client, bucket_name=config.s3_bucket_name))
    )
    claude_client = ClaudeClient(
        api_key=config.anthropic_api_key,
        model=config.anthropic_model,
        max_tokens=config.anthropic_max_tokens,
        timeout_seconds=config.anthropic_request_timeout_seconds,
        max_retries=config.anthropic_max_retries,
    )

    application.bot_data["allowed_user_ids"] = config.allowed_user_ids
    application.bot_data["config"] = config
    application.bot_data["storage"] = storage
    application.bot_data["workflow_manager"] = workflow_manager
    application.bot_data["conversation_manager"] = conversation_manager
    application.bot_data["analysis_service"] = AnalysisService(
        model=config.anthropic_model,
        schema_version=config.analysis_schema_version,
        storage=storage,
        claude_client=claude_client,
        analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["clarification_service"] = ClarificationService(
        max_questions=config.max_clarification_questions,
        claude_client=claude_client,
        analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["content_planning_service"] = ContentPlanningService(
        model=config.anthropic_model,
        schema_version=config.content_plan_schema_version,
        max_outputs=config.max_planned_outputs,
        claude_client=claude_client,
        analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager,
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["draft_generation_service"] = DraftGenerationService(
        model=config.anthropic_model,
        schema_version=config.draft_schema_version,
        max_instagram_caption_length=config.max_instagram_caption_length,
        max_hashtags=config.max_hashtags,
        claude_client=claude_client,
        analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager,
        draft_manager=draft_manager,
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["draft_manager"] = draft_manager
    application.bot_data["draft_version_manager"] = draft_version_manager
    application.bot_data["draft_editing_service"] = DraftEditingService(
        schema_version=config.draft_schema_version,
        max_instagram_caption_length=config.max_instagram_caption_length,
        max_hashtags=config.max_hashtags,
        claude_client=claude_client,
        analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager,
        draft_manager=draft_manager,
        draft_version_manager=draft_version_manager,
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["publication_manager"] = publication_manager
    application.bot_data["publication_preparation_service"] = PublicationPreparationService(
        schema_version=config.publication_schema_version,
        max_instagram_caption_length=config.max_instagram_caption_length,
        max_hashtags=config.max_hashtags,
        s3_bucket_name=config.s3_bucket_name,
        workflow_manager=workflow_manager,
        draft_manager=draft_manager,
        draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager,
        publication_manager=publication_manager,
        conversation_manager=conversation_manager,
    )
    application.bot_data["execution_manager"] = execution_manager
    application.bot_data["execution_service"] = ExecutionService(
        workflow_manager=workflow_manager,
        publication_manager=publication_manager,
        execution_manager=execution_manager,
        conversation_manager=conversation_manager,
    )

    meta_http_client = HttpxMetaHttpClient()
    instagram_publisher = InstagramPublisher(
        http_client=meta_http_client,
        media_access=S3PresignedMediaAccess(s3_client),
        clock=SystemClock(),
        instagram_account_id=config.instagram_account_id,
        access_token=config.meta_access_token,
        api_version=config.meta_graph_api_version,
        media_url_ttl_seconds=config.instagram_media_url_ttl_seconds,
        request_timeout_seconds=config.meta_request_timeout_seconds,
    )
    publisher_registry = PublisherRegistry({"instagram": instagram_publisher})
    application.bot_data["instagram_diagnostics_service"] = InstagramDiagnosticsService(
        http_client=meta_http_client,
        publishing_enabled=config.instagram_publishing_enabled,
        instagram_account_id=config.instagram_account_id,
        access_token=config.meta_access_token,
        api_version=config.meta_graph_api_version,
        request_timeout_seconds=config.meta_request_timeout_seconds,
    )
    execution_dispatch_service = ExecutionDispatchService(
        workflow_manager=workflow_manager,
        publication_manager=publication_manager,
        execution_manager=execution_manager,
        conversation_manager=conversation_manager,
        publisher_registry=publisher_registry,
        clock=SystemClock(),
        sleeper=AsyncioSleeper(),
        lease_seconds=config.dispatch_lease_seconds,
        poll_interval_seconds=config.instagram_container_poll_interval_seconds,
        poll_timeout_seconds=config.instagram_container_poll_timeout_seconds,
    )
    application.bot_data["execution_dispatch_service"] = execution_dispatch_service
    application.bot_data["live_validation_service"] = ControlledLiveValidationService(
        workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
        publication_manager=publication_manager,
        execution_manager=execution_manager,
        execution_dispatch_service=execution_dispatch_service,
        diagnostics_service=application.bot_data["instagram_diagnostics_service"],
        clock=SystemClock(),
        instagram_account_id=config.instagram_account_id,
        live_test_operator_ids=config.instagram_live_test_operator_ids,
    )

    application.bot_data["seen_updates"] = SeenUpdateTracker()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("retry", retry))
    application.add_handler(CommandHandler("instagram_status", instagram_status))
    application.add_handler(CommandHandler("instagram_test_publish", instagram_test_publish))
    application.add_handler(CallbackQueryHandler(review_action, pattern=r"^review:"))
    application.add_handler(CallbackQueryHandler(dispatch_action, pattern=r"^dispatch:"))
    application.add_handler(CallbackQueryHandler(instagram_live_validation_action, pattern=r"^ilv:"))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, media))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_reply))
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.PHOTO & ~filters.VIDEO & ~filters.TEXT,
            unhandled,
        )
    )

    return application


def main() -> None:
    # Local-dev convenience only: loads a .env file into the environment if
    # one exists, without overriding variables already set (e.g. by
    # JustRunMy.App or Docker). Config still reads exclusively from os.environ.
    load_dotenv()

    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR)
        logging.getLogger(__name__).error("Startup failed: %s", exc)
        raise SystemExit(1) from exc

    configure_logging(config.log_level)
    logger.info("Starting NRC Social Agent (environment=%s)", config.environment)

    application = build_application(config)
    application.run_polling()


if __name__ == "__main__":
    main()
