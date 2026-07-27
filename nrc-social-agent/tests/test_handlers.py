from unittest.mock import AsyncMock, MagicMock

import pytest

from src import handlers
from src.ai.clarification_service import ClarificationOutcome
from src.ai.clarification_models import ClarificationDecisionType
from src.ai.content_planning_service import ContentPlanOutcome
from src.ai.content_plan_models import PlannedOutput
from src.ai.draft_editing_service import DraftEditOutcome
from src.ai.draft_generation_service import DraftGenerationOutcome
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.ai.errors import (
    AnalysisError,
    ClarificationContextInsufficientError,
    ContentPlanValidationFailedError,
    DraftEditValidationFailedError,
    DraftValidationFailedError,
    UnsupportedMediaTypeForAnalysisError,
    WorkflowNotEligibleForPlanningError,
    WorkflowNotWaitingForReplyError,
)
from src.conversation.errors import NoActiveWorkflowError, StaleWorkflowPointerError
from src.media.errors import MediaIngestionError, UnsupportedMediaTypeError
from src.media.ingestion import MediaIngestionResult
from src.media.types import MediaType
from src.utils.dedup import SeenUpdateTracker
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


def _make_current_draft_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="abc123", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=1, prompt_version=1, model="claude-opus-5",
        content={"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1,
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def _make_current_pointer(**overrides):
    defaults = dict(
        workflow_id="abc123", output_id="out-1", current_draft_id="draft-1", current_version_number=1,
        status=ReviewStatus.READY_FOR_REVIEW, updated_at="x",
    )
    defaults.update(overrides)
    return CurrentDraftPointer(**defaults)


def _make_output(output_type="instagram_reel_caption", priority=1):
    return PlannedOutput(
        output_id="out-1",
        output_type=output_type,
        priority=priority,
        purpose="build founder credibility",
        message_focus="make the idea immediate and memorable",
        cta_direction="invite viewers to explore NRC without a hard sell",
    )


def _make_update_and_context(user_id, allowed_ids, update_id=1, text=None):
    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.effective_message.reply_text = AsyncMock()
    update.effective_message.text = text

    analysis_service = MagicMock()
    analysis_service.analyze_workflow = AsyncMock(return_value=None)

    clarification_service = MagicMock()
    clarification_service.evaluate_and_advance = AsyncMock(
        return_value=ClarificationOutcome(
            workflow_id="abc123", decision=ClarificationDecisionType.CONTINUE, question_text=None
        )
    )
    clarification_service.handle_user_reply = AsyncMock(
        return_value=ClarificationOutcome(
            workflow_id="abc123", decision=ClarificationDecisionType.CONTINUE, question_text=None
        )
    )

    content_planning_service = MagicMock()
    content_planning_service.plan_content = AsyncMock(
        return_value=ContentPlanOutcome(workflow_id="abc123", plan_id="plan-1", outputs=[_make_output()])
    )

    draft_generation_service = MagicMock()
    draft_generation_service.generate_draft = AsyncMock(
        return_value=DraftGenerationOutcome(
            workflow_id="abc123",
            draft_id="draft-1",
            output_id="out-1",
            output_type="instagram_reel_caption",
            content={"caption": "A great caption.", "hashtags": [], "cta": None},
        )
    )

    draft_manager = MagicMock()
    draft_version_manager = MagicMock()
    draft_version_manager.get_current.return_value = (
        _make_current_draft_document(), _make_current_pointer(), '"etag-1"',
    )

    draft_editing_service = MagicMock()
    draft_editing_service.edit_draft = AsyncMock(
        return_value=DraftEditOutcome(
            workflow_id="abc123", draft_id="draft-2", output_id="out-1",
            output_type="instagram_reel_caption", version_number=2,
            content={"caption": "A revised caption.", "hashtags": [], "cta": None},
        )
    )

    config = MagicMock()
    config.instagram_publishing_enabled = False
    config.instagram_live_test_operator_ids = frozenset()

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "config": config,
        "storage": MagicMock(),
        "workflow_manager": MagicMock(),
        "conversation_manager": MagicMock(),
        "analysis_service": analysis_service,
        "clarification_service": clarification_service,
        "content_planning_service": content_planning_service,
        "draft_generation_service": draft_generation_service,
        "draft_manager": draft_manager,
        "draft_version_manager": draft_version_manager,
        "draft_editing_service": draft_editing_service,
        "publication_preparation_service": MagicMock(),
        "execution_service": MagicMock(),
        "execution_manager": MagicMock(),
        "execution_dispatch_service": MagicMock(),
        "instagram_diagnostics_service": MagicMock(),
        "live_validation_service": MagicMock(),
    }
    return update, context


def _make_document(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


async def test_start_replies_for_authorized_user():
    update, context = _make_update_and_context(1, {1})

    await handlers.start(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.START_MESSAGE)


async def test_start_denied_for_unauthorized_user():
    update, context = _make_update_and_context(2, {1})

    await handlers.start(update, context)

    update.effective_message.reply_text.assert_awaited_once()
    (sent_text,), _ = update.effective_message.reply_text.call_args
    assert sent_text != handlers.START_MESSAGE


async def test_help_command_replies_with_help_message():
    update, context = _make_update_and_context(1, {1})

    await handlers.help_command(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.HELP_MESSAGE)


async def test_unhandled_message_replies_with_placeholder():
    update, context = _make_update_and_context(1, {1})

    await handlers.unhandled(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.UNHANDLED_MESSAGE)


async def test_all_handlers_deny_unauthorized_users(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock())
    update, context = _make_update_and_context(999, {1})

    for handler in (
        handlers.start,
        handlers.help_command,
        handlers.status,
        handlers.cancel,
        handlers.retry,
        handlers.unhandled,
        handlers.media,
        handlers.text_reply,
        handlers.instagram_status,
        handlers.instagram_test_publish,
        handlers.instagram_live_validation_action,
    ):
        update.effective_message.reply_text.reset_mock()
        await handler(update, context)
        update.effective_message.reply_text.assert_awaited_once_with(
            "Sorry, you're not authorized to use this bot."
        )


# --- status() ----------------------------------------------------------


async def test_status_reports_no_active_post_when_resolution_finds_none(monkeypatch):
    monkeypatch.setattr(
        handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none"))
    )
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        handlers.STATUS_MESSAGE_NO_ACTIVE_POST
    )


async def test_status_reports_state_and_workflow_id(monkeypatch):
    document = _make_document(workflow_id="wf-status", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "Waiting on your reply.\nReference: wf-status"
    )


async def test_status_reports_generating_content_state_description(monkeypatch):
    document = _make_document(workflow_id="wf-gen", state=WorkflowState.GENERATING_CONTENT)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "Ready to generate your content.\nReference: wf-gen"
    )


async def test_status_replies_with_generic_message_on_conversation_error(monkeypatch):
    error = StaleWorkflowPointerError("stale")
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=error))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)


# --- instagram_status() (Milestone 11A) -----------------------------------


def _make_diagnostics_result(**overrides):
    from src.publisher.instagram.diagnostics import InstagramDiagnosticsResult

    defaults = dict(
        application_healthy=True, publishing_enabled=True, instagram_configured=True,
        token_valid=True, permissions_ok=True, account_verified=True, account_username="nrc_official",
        instagram_ready=True, failure_stage=None, failure_reason=None,
    )
    defaults.update(overrides)
    return InstagramDiagnosticsResult(**defaults)


async def test_instagram_status_reports_ready_state():
    update, context = _make_update_and_context(1, {1})
    context.bot_data["instagram_diagnostics_service"].check_status.return_value = _make_diagnostics_result()

    await handlers.instagram_status(update, context)

    (message,), _ = update.effective_message.reply_text.call_args
    assert "Status: Ready to publish" in message
    assert "Token: Valid" in message
    assert "Permissions: Verified" in message
    assert "Account: Verified (@nrc_official)" in message
    assert "Publishing: Enabled" in message


async def test_instagram_status_reports_disabled_state_without_touching_any_workflow(monkeypatch):
    resolve_mock = MagicMock()
    monkeypatch.setattr(handlers, "resolve_active_workflow", resolve_mock)
    update, context = _make_update_and_context(1, {1})
    context.bot_data["instagram_diagnostics_service"].check_status.return_value = _make_diagnostics_result(
        publishing_enabled=False, instagram_configured=False, token_valid=None, permissions_ok=None,
        account_verified=None, account_username=None, instagram_ready=False,
        failure_stage="publishing_disabled", failure_reason="Instagram publishing is not enabled",
    )

    await handlers.instagram_status(update, context)

    (message,), _ = update.effective_message.reply_text.call_args
    assert "Not ready — Instagram publishing is not enabled" in message
    assert "Publishing: Disabled" in message
    # Diagnostics never resolve or touch any workflow.
    resolve_mock.assert_not_called()
    assert context.bot_data["workflow_manager"].mock_calls == []


async def test_instagram_status_never_mutates_execution_or_publication_managers():
    update, context = _make_update_and_context(1, {1})
    context.bot_data["instagram_diagnostics_service"].check_status.return_value = _make_diagnostics_result()

    await handlers.instagram_status(update, context)

    assert context.bot_data["execution_manager"].mock_calls == []
    assert context.bot_data["execution_service"].mock_calls == []
    assert context.bot_data["publication_preparation_service"].mock_calls == []


async def test_instagram_status_denies_unauthorized_user():
    update, context = _make_update_and_context(999, {1})

    await handlers.instagram_status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with("Sorry, you're not authorized to use this bot.")


# --- cancel() ------------------------------------------------------------


async def test_cancel_reports_nothing_to_cancel_when_no_active_workflow(monkeypatch):
    monkeypatch.setattr(
        handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none"))
    )
    update, context = _make_update_and_context(1, {1})

    await handlers.cancel(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        handlers.CANCEL_MESSAGE_NOTHING_TO_CANCEL
    )
    context.bot_data["workflow_manager"].update_state.assert_not_called()


async def test_cancel_rejects_active_workflow_and_clears_pointer(monkeypatch):
    document = _make_document(workflow_id="wf-cancel", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.cancel(update, context)

    context.bot_data["workflow_manager"].update_state.assert_called_once_with(
        "wf-cancel", WorkflowState.REJECTED
    )
    context.bot_data["conversation_manager"].clear_active_workflow.assert_called_once_with(1)
    update.effective_message.reply_text.assert_awaited_once_with(
        "Post cancelled.\nReference: wf-cancel"
    )


async def test_cancel_on_already_terminal_workflow_clears_pointer_without_rejecting(monkeypatch):
    document = _make_document(workflow_id="wf-done", state=WorkflowState.COMPLETED)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.cancel(update, context)

    context.bot_data["workflow_manager"].update_state.assert_not_called()
    context.bot_data["conversation_manager"].clear_active_workflow.assert_called_once_with(1)
    update.effective_message.reply_text.assert_awaited_once_with(
        handlers.CANCEL_MESSAGE_NOTHING_TO_CANCEL
    )


async def test_cancel_replies_with_generic_message_when_update_state_fails(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})
    error = WorkflowPersistenceError("s3 down")
    context.bot_data["workflow_manager"].update_state.side_effect = error

    await handlers.cancel(update, context)

    context.bot_data["conversation_manager"].clear_active_workflow.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)


# --- media() -----------------------------------------------------------


async def test_media_denies_unauthorized_user_without_calling_ingest_media(monkeypatch):
    ingest_media = AsyncMock()
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(999, {1})

    await handlers.media(update, context)

    ingest_media.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(
        "Sorry, you're not authorized to use this bot."
    )


async def test_media_replies_with_uploaded_message_then_full_chain_to_draft_preview(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    ingest_media = AsyncMock(return_value=result)
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    ingest_media.assert_awaited_once()
    context.bot_data["analysis_service"].analyze_workflow.assert_awaited_once_with(
        workflow_id="abc123", telegram_user_id=1
    )
    context.bot_data["clarification_service"].evaluate_and_advance.assert_awaited_once_with(
        workflow_id="abc123", telegram_user_id=1
    )
    context.bot_data["content_planning_service"].plan_content.assert_awaited_once_with(
        workflow_id="abc123", telegram_user_id=1
    )
    context.bot_data["draft_generation_service"].generate_draft.assert_awaited_once_with(
        workflow_id="abc123", telegram_user_id=1
    )
    assert update.effective_message.reply_text.await_count == 6
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == (
        "Got it — your photo was uploaded successfully.\nReference: abc123\n\nAnalyzing your content…"
    )
    assert calls[1].args[0] == handlers.CLARIFICATION_SUFFICIENT_MESSAGE
    assert calls[2].args[0] == handlers.PLANNING_STARTED_MESSAGE
    assert calls[3].args[0] == "Content plan ready.\n\nRecommended: Instagram caption\n\nNext, I'll prepare the first draft."
    assert calls[4].args[0] == handlers.DRAFT_GENERATION_STARTED_MESSAGE
    assert calls[5].args[0] == (
        "Here's a draft ready for your review:\n\nVersion 1\n\nA great caption."
        "\n\nThis is ready for your review — nothing has been published."
    )
    assert calls[5].kwargs["reply_markup"] is not None


async def test_media_sends_the_adaptive_question_when_clarification_required(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    context.bot_data["clarification_service"].evaluate_and_advance = AsyncMock(
        return_value=ClarificationOutcome(
            workflow_id="abc123",
            decision=ClarificationDecisionType.ASK_QUESTION,
            question_text="Is this mainly for founders, or a broader audience?",
        )
    )

    await handlers.media(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == "Is this mainly for founders, or a broader audience?"
    context.bot_data["content_planning_service"].plan_content.assert_not_called()


async def test_media_rejects_new_upload_when_a_workflow_is_already_in_progress(monkeypatch):
    document = _make_document(workflow_id="wf-active", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    ingest_media = AsyncMock()
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    ingest_media.assert_not_called()
    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(
        "You already have a post in progress.\nReference: wf-active\n\n"
        "Please /cancel it first if you'd like to start a new one."
    )


async def test_media_rejects_new_upload_while_generating_content(monkeypatch):
    # docs/WORKFLOW.md §5: GENERATING_CONTENT (planning in progress or
    # about to start) is not terminal — a new upload must still be refused.
    document = _make_document(workflow_id="wf-planning", state=WorkflowState.GENERATING_CONTENT)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    ingest_media = AsyncMock()
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    ingest_media.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(
        "You already have a post in progress.\nReference: wf-planning\n\n"
        "Please /cancel it first if you'd like to start a new one."
    )


async def test_media_allows_new_upload_when_prior_workflow_is_terminal(monkeypatch):
    document = _make_document(workflow_id="wf-done", state=WorkflowState.COMPLETED)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    ingest_media = AsyncMock(return_value=result)
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    ingest_media.assert_awaited_once()


async def test_media_replies_with_generic_message_on_ingestion_error(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    error = UnsupportedMediaTypeError("bad mime type")
    ingest_media = AsyncMock(side_effect=error)
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)
    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()


async def test_media_replies_with_generic_message_on_unexpected_ingestion_exception(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    ingest_media = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1})

    await handlers.media(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        MediaIngestionError.user_message
    )
    # The unexpected exception's real detail must never reach the user.
    (sent_text,), _ = update.effective_message.reply_text.call_args
    assert "boom" not in sent_text
    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()


async def test_media_replies_with_specific_message_on_unsupported_media_type_for_analysis(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.VIDEO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    error = UnsupportedMediaTypeForAnalysisError("video not supported")
    context.bot_data["analysis_service"].analyze_workflow = AsyncMock(side_effect=error)

    await handlers.media(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    context.bot_data["clarification_service"].evaluate_and_advance.assert_not_called()


async def test_media_replies_with_generic_message_on_analysis_error(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    error = AnalysisError("claude timed out")
    context.bot_data["analysis_service"].analyze_workflow = AsyncMock(side_effect=error)

    await handlers.media(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    assert "claude timed out" not in second_call.args[0]


async def test_media_replies_with_generic_message_on_unexpected_analysis_exception(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    context.bot_data["analysis_service"].analyze_workflow = AsyncMock(side_effect=RuntimeError("boom"))

    await handlers.media(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == AnalysisError.user_message
    assert "boom" not in second_call.args[0]


async def test_media_replies_with_generic_message_on_clarification_error(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    error = ClarificationContextInsufficientError("limit reached")
    context.bot_data["clarification_service"].evaluate_and_advance = AsyncMock(side_effect=error)

    await handlers.media(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    context.bot_data["content_planning_service"].plan_content.assert_not_called()


async def test_media_replies_with_generic_message_on_planning_error(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    result = MediaIngestionResult(workflow_id="abc123", media_type=MediaType.PHOTO)
    monkeypatch.setattr(handlers, "ingest_media", AsyncMock(return_value=result))

    update, context = _make_update_and_context(1, {1})
    error = ContentPlanValidationFailedError("too generic")
    context.bot_data["content_planning_service"].plan_content = AsyncMock(side_effect=error)

    await handlers.media(update, context)

    calls = update.effective_message.reply_text.await_args_list
    # upload ack, sufficient, planning started, then the failure message
    assert calls[2].args[0] == handlers.PLANNING_STARTED_MESSAGE
    assert calls[3].args[0] == error.user_message
    assert "too generic" not in calls[3].args[0]
    context.bot_data["draft_generation_service"].generate_draft.assert_not_called()


async def test_media_skips_duplicate_update_without_calling_ingest_media(monkeypatch):
    ingest_media = AsyncMock()
    monkeypatch.setattr(handlers, "ingest_media", ingest_media)

    update, context = _make_update_and_context(1, {1}, update_id=42)
    context.bot_data["seen_updates"].seen_before(42)  # simulate already-processed

    await handlers.media(update, context)

    ingest_media.assert_not_called()
    update.effective_message.reply_text.assert_not_called()


# --- text_reply() --------------------------------------------------------


async def test_text_reply_denies_unauthorized_user():
    update, context = _make_update_and_context(999, {1}, text="hello")

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "Sorry, you're not authorized to use this bot."
    )
    context.bot_data["clarification_service"].handle_user_reply.assert_not_called()


async def test_text_reply_with_no_active_workflow_gets_safe_response_and_calls_no_claude(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    update, context = _make_update_and_context(1, {1}, text="hello")

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.TEXT_REPLY_NO_ACTIVE_WORKFLOW_MESSAGE)
    context.bot_data["clarification_service"].handle_user_reply.assert_not_called()


async def test_text_reply_against_non_waiting_workflow_does_not_call_claude(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, text="hello")

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.TEXT_REPLY_NOT_WAITING_MESSAGE)
    context.bot_data["clarification_service"].handle_user_reply.assert_not_called()


async def test_text_reply_resolves_active_workflow_and_chains_to_planning(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, text="Mostly credibility, no hard sell.")

    await handlers.text_reply(update, context)

    context.bot_data["clarification_service"].handle_user_reply.assert_awaited_once_with(
        workflow_id="wf-1",
        telegram_user_id=1,
        reply_text="Mostly credibility, no hard sell.",
        telegram_update_id=1,
    )
    context.bot_data["content_planning_service"].plan_content.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    context.bot_data["draft_generation_service"].generate_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.CLARIFICATION_SUFFICIENT_MESSAGE
    assert calls[1].args[0] == handlers.PLANNING_STARTED_MESSAGE
    assert calls[3].args[0] == handlers.DRAFT_GENERATION_STARTED_MESSAGE


async def test_text_reply_sends_the_next_adaptive_question(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, text="NRC")
    context.bot_data["clarification_service"].handle_user_reply = AsyncMock(
        return_value=ClarificationOutcome(
            workflow_id="wf-1", decision=ClarificationDecisionType.ASK_QUESTION, question_text="And the tone?"
        )
    )

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_awaited_once_with("And the tone?")
    context.bot_data["content_planning_service"].plan_content.assert_not_called()


async def test_text_reply_duplicate_reply_sends_nothing(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, text="NRC")
    context.bot_data["clarification_service"].handle_user_reply = AsyncMock(return_value=None)

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_not_called()


async def test_text_reply_replies_with_generic_message_on_clarification_error(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, text="NRC")
    error = WorkflowNotWaitingForReplyError("race condition")
    context.bot_data["clarification_service"].handle_user_reply = AsyncMock(side_effect=error)

    await handlers.text_reply(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)


async def test_text_reply_skips_duplicate_update_without_calling_clarification_service(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.WAITING_FOR_USER)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1}, update_id=42, text="NRC")
    context.bot_data["seen_updates"].seen_before(42)

    await handlers.text_reply(update, context)

    context.bot_data["clarification_service"].handle_user_reply.assert_not_called()
    update.effective_message.reply_text.assert_not_called()


# --- retry() ---------------------------------------------------------------


async def test_retry_reports_nothing_to_retry_when_no_active_workflow(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.RETRY_MESSAGE_NOTHING_TO_RETRY)


async def test_retry_reports_nothing_to_retry_when_no_pending_retry_marker(monkeypatch):
    document = _make_document(workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA, metadata={})
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.RETRY_MESSAGE_NOTHING_TO_RETRY)
    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()
    context.bot_data["clarification_service"].evaluate_and_advance.assert_not_called()
    context.bot_data["content_planning_service"].plan_content.assert_not_called()


async def test_retry_reattempts_analysis_then_clarification_then_planning(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA, metadata={"pending_retry": "analysis"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    context.bot_data["analysis_service"].analyze_workflow.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    context.bot_data["clarification_service"].evaluate_and_advance.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    context.bot_data["content_planning_service"].plan_content.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    context.bot_data["draft_generation_service"].generate_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )


async def test_retry_reattempts_clarification_decision_only(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA, metadata={"pending_retry": "clarification_decision"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()
    context.bot_data["clarification_service"].evaluate_and_advance.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )


async def test_retry_reattempts_content_planning_only(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.GENERATING_CONTENT, metadata={"pending_retry": "content_planning"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()
    context.bot_data["clarification_service"].evaluate_and_advance.assert_not_called()
    context.bot_data["content_planning_service"].plan_content.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    context.bot_data["draft_generation_service"].generate_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.PLANNING_STARTED_MESSAGE


async def test_retry_sends_the_adaptive_question_when_produced(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA, metadata={"pending_retry": "clarification_decision"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})
    context.bot_data["clarification_service"].evaluate_and_advance = AsyncMock(
        return_value=ClarificationOutcome(
            workflow_id="wf-1", decision=ClarificationDecisionType.ASK_QUESTION, question_text="Which platform?"
        )
    )

    await handlers.retry(update, context)

    update.effective_message.reply_text.assert_awaited_once_with("Which platform?")
    context.bot_data["content_planning_service"].plan_content.assert_not_called()


async def test_retry_replies_with_generic_message_when_analysis_retry_fails(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.ANALYZING_MEDIA, metadata={"pending_retry": "analysis"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})
    error = AnalysisError("still failing")
    context.bot_data["analysis_service"].analyze_workflow = AsyncMock(side_effect=error)

    await handlers.retry(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)
    context.bot_data["clarification_service"].evaluate_and_advance.assert_not_called()


async def test_retry_replies_with_generic_message_when_planning_retry_fails(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.GENERATING_CONTENT, metadata={"pending_retry": "content_planning"}
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})
    error = WorkflowNotEligibleForPlanningError("race condition")
    context.bot_data["content_planning_service"].plan_content = AsyncMock(side_effect=error)

    await handlers.retry(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    context.bot_data["draft_generation_service"].generate_draft.assert_not_called()


# --- retry(): primary_draft_generation ---------------------------------


async def test_retry_reattempts_primary_draft_generation_only(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.GENERATING_CONTENT,
        metadata={"pending_retry": "primary_draft_generation"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.retry(update, context)

    context.bot_data["analysis_service"].analyze_workflow.assert_not_called()
    context.bot_data["clarification_service"].evaluate_and_advance.assert_not_called()
    context.bot_data["content_planning_service"].plan_content.assert_not_called()
    context.bot_data["draft_generation_service"].generate_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.DRAFT_GENERATION_STARTED_MESSAGE


async def test_retry_replies_with_generic_message_when_draft_generation_retry_fails(monkeypatch):
    document = _make_document(
        workflow_id="wf-1", state=WorkflowState.GENERATING_CONTENT,
        metadata={"pending_retry": "primary_draft_generation"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})
    error = DraftValidationFailedError("too generic")
    context.bot_data["draft_generation_service"].generate_draft = AsyncMock(side_effect=error)

    await handlers.retry(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    assert "too generic" not in second_call.args[0]


# --- status(): SHOWING_PREVIEW ------------------------------------------


async def test_status_reports_showing_preview_state_description(monkeypatch):
    document = _make_document(workflow_id="wf-preview", state=WorkflowState.SHOWING_PREVIEW)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        "Your draft is ready for review.\nReference: wf-preview"
    )


# --- status(): COMPLETED / Publication Preparation Layer (Milestone 8) --


async def test_status_reports_publication_ready_description(monkeypatch):
    document = _make_document(workflow_id="wf-done", state=WorkflowState.COMPLETED, publication=None)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        f"{handlers.STATE_DESCRIPTIONS[WorkflowState.COMPLETED]}.\nReference: wf-done"
    )


async def test_status_reports_publication_retry_pending_description(monkeypatch):
    document = _make_document(
        workflow_id="wf-done", state=WorkflowState.COMPLETED,
        metadata={"pending_retry": "publication_preparation"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        f"{handlers.STATUS_PUBLICATION_RETRY_PENDING_DESCRIPTION}.\nReference: wf-done"
    )


async def test_status_reports_publication_permanently_unavailable_description(monkeypatch):
    document = _make_document(
        workflow_id="wf-done", state=WorkflowState.COMPLETED,
        publication={"status": "FAILED"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_update_and_context(1, {1})

    await handlers.status(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(
        f"{handlers.STATUS_PUBLICATION_PERMANENTLY_UNAVAILABLE_DESCRIPTION}.\nReference: wf-done"
    )
    assert "PUBLISHED" not in update.effective_message.reply_text.await_args_list[0].args[0]


# --- draft preview formatting --------------------------------------------


def test_format_draft_preview_message_includes_header_and_footer():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption", {"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1
    )

    assert message == (
        "Here's a draft ready for your review:\n\nVersion 1\n\nA great caption."
        "\n\nThis is ready for your review — nothing has been published."
    )


def test_format_draft_preview_message_uses_updated_draft_header_for_later_versions():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption", {"caption": "A revised caption.", "hashtags": [], "cta": None}, version_number=2
    )

    assert message.startswith("Updated draft — Version 2:")
    assert "Version 2" in message


def test_format_draft_preview_message_never_says_published_without_the_review_framing():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption", {"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1
    )

    assert "ready for your review" in message.lower()


def test_format_draft_preview_message_includes_hashtags_and_cta():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption",
        {"caption": "A great caption.", "hashtags": ["nrc", "branding"], "cta": "Explore NRC's work."},
        version_number=1,
    )

    assert "Explore NRC's work." in message
    assert "#nrc" in message
    assert "#branding" in message


def test_format_draft_preview_message_omits_empty_hashtags_and_cta():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption", {"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1
    )

    assert "#" not in message


def test_format_draft_preview_message_never_exposes_internal_fields():
    message = handlers._format_draft_preview_message(
        "instagram_reel_caption", {"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1
    )

    for banned in ("draft_id", "output_id", "schema_version", "model", "token"):
        assert banned not in message.lower()


# --- review-keyboard / callback-data parsing -------------------------------


def test_build_review_keyboard_contains_all_four_actions():
    keyboard = handlers._build_review_keyboard("wf-1", 1)

    callback_data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callback_data == ["review:approve:wf-1:1", "review:edit:wf-1:1", "review:save:wf-1:1", "review:reject:wf-1:1"]


def test_parse_review_callback_data_valid():
    assert handlers._parse_review_callback_data("review:approve:wf-1:2") == ("approve", "wf-1", 2)


def test_parse_review_callback_data_rejects_unknown_action():
    assert handlers._parse_review_callback_data("review:publish:wf-1:2") is None


def test_parse_review_callback_data_rejects_non_integer_version():
    assert handlers._parse_review_callback_data("review:approve:wf-1:abc") is None


def test_parse_review_callback_data_rejects_wrong_field_count():
    assert handlers._parse_review_callback_data("review:approve:wf-1") is None


def test_parse_review_callback_data_rejects_non_review_prefix():
    assert handlers._parse_review_callback_data("other:approve:wf-1:1") is None


def test_parse_review_callback_data_rejects_none():
    assert handlers._parse_review_callback_data(None) is None


def test_parse_review_callback_data_rejects_zero_or_negative_version():
    assert handlers._parse_review_callback_data("review:approve:wf-1:0") is None
    assert handlers._parse_review_callback_data("review:approve:wf-1:-1") is None


# --- draft-generation chain and preview delivery -------------------------


def _make_draft_version_manager_stub(**pointer_overrides):
    draft_version_manager = MagicMock()
    draft_version_manager.get_current.return_value = (
        _make_current_draft_document(workflow_id="wf-1", output_id="out-1"),
        _make_current_pointer(workflow_id="wf-1", output_id="out-1", **pointer_overrides),
        '"etag-1"',
    )
    return draft_version_manager


async def test_run_draft_generation_and_reply_sends_started_message_then_preview():
    message = MagicMock()
    message.reply_text = AsyncMock()
    draft_generation_service = MagicMock()
    draft_generation_service.generate_draft = AsyncMock(
        return_value=DraftGenerationOutcome(
            workflow_id="wf-1", draft_id="draft-1", output_id="out-1",
            output_type="instagram_reel_caption",
            content={"caption": "A great caption.", "hashtags": [], "cta": None},
        )
    )
    draft_version_manager = _make_draft_version_manager_stub()
    draft_manager = MagicMock()

    await handlers._run_draft_generation_and_reply(
        draft_generation_service, draft_version_manager, draft_manager, message,
        workflow_id="wf-1", telegram_user_id=1,
    )

    draft_generation_service.generate_draft.assert_awaited_once_with(workflow_id="wf-1", telegram_user_id=1)
    calls = message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.DRAFT_GENERATION_STARTED_MESSAGE
    assert "A great caption." in calls[1].args[0]


async def test_run_draft_generation_and_reply_sends_generic_message_on_failure():
    message = MagicMock()
    message.reply_text = AsyncMock()
    draft_generation_service = MagicMock()
    error = DraftValidationFailedError("too generic")
    draft_generation_service.generate_draft = AsyncMock(side_effect=error)
    draft_version_manager = _make_draft_version_manager_stub()
    draft_manager = MagicMock()

    await handlers._run_draft_generation_and_reply(
        draft_generation_service, draft_version_manager, draft_manager, message,
        workflow_id="wf-1", telegram_user_id=1,
    )

    _, second_call = message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    assert "too generic" not in second_call.args[0]


async def test_run_draft_generation_and_reply_survives_preview_send_failure():
    # A preview-send failure must never be treated as a generation failure
    # (draft/state already persisted+transitioned by the service) — no
    # exception should propagate out of this helper.
    message = MagicMock()
    message.reply_text = AsyncMock(side_effect=[None, RuntimeError("telegram unavailable")])
    draft_generation_service = MagicMock()
    draft_generation_service.generate_draft = AsyncMock(
        return_value=DraftGenerationOutcome(
            workflow_id="wf-1", draft_id="draft-1", output_id="out-1",
            output_type="instagram_reel_caption",
            content={"caption": "A great caption.", "hashtags": [], "cta": None},
        )
    )
    draft_version_manager = _make_draft_version_manager_stub()
    draft_manager = MagicMock()

    await handlers._run_draft_generation_and_reply(
        draft_generation_service, draft_version_manager, draft_manager, message,
        workflow_id="wf-1", telegram_user_id=1,
    )

    assert message.reply_text.await_count == 2


# --- content-plan message formatting ---------------------------------------


def test_format_plan_ready_message_single_output_reads_complete():
    message = handlers._format_plan_ready_message([_make_output()])

    assert message == (
        "Content plan ready.\n\nRecommended: Instagram caption\n\nNext, I'll prepare the first draft."
    )
    assert "•" not in message


def test_format_plan_ready_message_multi_output_lists_bullets():
    outputs = [_make_output("instagram_reel_caption", 1), _make_output("linkedin_post", 2)]

    message = handlers._format_plan_ready_message(outputs)

    assert "• Instagram caption" in message
    assert "• LinkedIn post" in message
    assert message.startswith("Content plan ready.")
    assert message.endswith("Next, I'll prepare the first draft.")


def test_format_plan_ready_message_never_exposes_priority_or_purpose():
    outputs = [_make_output()]

    message = handlers._format_plan_ready_message(outputs)

    assert "priority" not in message.lower()
    assert "build founder credibility" not in message
    assert "out-1" not in message
