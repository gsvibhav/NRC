from unittest.mock import AsyncMock, MagicMock

import pytest

from src import handlers
from src.execution.errors import ExecutionError, UnauthorizedDispatchError
from src.execution.models import DispatchCheckpoint, ExecutionStatus
from src.utils.dedup import SeenUpdateTracker
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


def _make_workflow_document(**overrides):
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=1, created_at="x", updated_at="x",
        state=WorkflowState.COMPLETED, media={},
        publication={"publication_id": "pub-1", "output_id": "out-1", "status": "READY_FOR_PUBLISHING"},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_dispatch_update_and_context(user_id, allowed_ids, *, callback_data, update_id=1, instagram_publishing_enabled=True):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.callback_query = query
    update.effective_message = query.message

    config = MagicMock()
    config.instagram_publishing_enabled = instagram_publishing_enabled

    execution_dispatch_service = MagicMock()

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "config": config,
        "execution_dispatch_service": execution_dispatch_service,
    }
    return update, context, query


def _make_outcome(**overrides):
    from src.execution.dispatch_service import DispatchOutcome

    defaults = dict(status="completed", execution_id="exec-1", publication_id="pub-1", result={"external_media_id": "m1"})
    defaults.update(overrides)
    return DispatchOutcome(**defaults)


# --- dispatch_action(): authorization / malformed / disabled --------------


async def test_dispatch_action_denies_unauthorized_user():
    update, context, query = _make_dispatch_update_and_context(999, {1}, callback_data="dispatch:instagram:pub-1")

    await handlers.dispatch_action(update, context)

    query.message.reply_text.assert_awaited_once_with("Sorry, you're not authorized to use this bot.")


async def test_dispatch_action_skips_duplicate_update():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1", update_id=42)
    context.bot_data["seen_updates"].seen_before(42)

    await handlers.dispatch_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()


async def test_dispatch_action_reports_disabled_with_zero_dispatch_calls():
    update, context, query = _make_dispatch_update_and_context(
        1, {1}, callback_data="dispatch:instagram:pub-1", instagram_publishing_enabled=False
    )

    await handlers.dispatch_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.INSTAGRAM_PUBLISHING_DISABLED_MESSAGE)
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch.assert_not_called()


async def test_dispatch_action_rejects_malformed_callback_data():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:linkedin:pub-1")

    await handlers.dispatch_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch.assert_not_called()


# --- dispatch_action(): success/failure outcomes --------------------------


async def test_dispatch_action_success_reports_completed():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(return_value=_make_outcome())

    await handlers.dispatch_action(update, context)

    context.bot_data["execution_dispatch_service"].authorize_and_dispatch.assert_awaited_once_with(
        publication_id="pub-1", telegram_user_id=1
    )
    calls = query.message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.DISPATCH_STARTED_MESSAGE
    assert calls[1].args[0] == handlers.DISPATCH_COMPLETED_MESSAGE


async def test_dispatch_action_success_includes_permalink_when_present():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    outcome = _make_outcome(result={"external_media_id": "m1", "permalink": "https://instagram.com/p/abc"})
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(return_value=outcome)

    await handlers.dispatch_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert "https://instagram.com/p/abc" in calls[1].args[0]


async def test_dispatch_action_retryable_failure_reports_safe_message():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(
        return_value=_make_outcome(status="retryable_failure", result=None)
    )

    await handlers.dispatch_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == handlers.DISPATCH_RETRYABLE_FAILURE_MESSAGE


async def test_dispatch_action_permanent_failure_reports_safe_message_never_raw_error():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(
        return_value=_make_outcome(status="permanent_failure", result=None, failure={"safe_message": "internal detail"})
    )

    await handlers.dispatch_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == handlers.DISPATCH_PERMANENT_FAILURE_MESSAGE
    assert "internal detail" not in calls[1].args[0]


async def test_dispatch_action_unauthorized_dispatch_reports_generic_unavailable():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(
        side_effect=UnauthorizedDispatchError("not your workflow")
    )

    await handlers.dispatch_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == handlers.REVIEW_ACTION_UNAVAILABLE_MESSAGE


async def test_dispatch_action_execution_error_reports_its_own_user_message():
    update, context, query = _make_dispatch_update_and_context(1, {1}, callback_data="dispatch:instagram:pub-1")
    error = ExecutionError("boom")
    context.bot_data["execution_dispatch_service"].authorize_and_dispatch = AsyncMock(side_effect=error)

    await handlers.dispatch_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == error.user_message


# --- callback data parsing / keyboard building ----------------------------


def test_parse_dispatch_callback_data_valid():
    assert handlers._parse_dispatch_callback_data("dispatch:instagram:pub-1") == ("instagram", "pub-1")


def test_parse_dispatch_callback_data_rejects_wrong_prefix():
    assert handlers._parse_dispatch_callback_data("review:approve:wf-1:1") is None


def test_parse_dispatch_callback_data_rejects_unknown_publisher():
    assert handlers._parse_dispatch_callback_data("dispatch:threads:pub-1") is None


def test_build_dispatch_keyboard_never_includes_a_token_or_account_id():
    markup = handlers._build_dispatch_keyboard("pub-1")
    callback_data = markup.inline_keyboard[0][0].callback_data
    assert callback_data == "dispatch:instagram:pub-1"


# --- /retry: publication_dispatch ------------------------------------------


async def test_retry_reattempts_publication_dispatch_only(monkeypatch):
    document = _make_workflow_document(metadata={"pending_retry": "publication_dispatch"})
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))

    update = MagicMock()
    update.update_id = 1
    update.effective_user.id = 1
    update.effective_message.reply_text = AsyncMock()

    config = MagicMock()
    config.instagram_publishing_enabled = True
    execution_dispatch_service = MagicMock()
    execution_dispatch_service.retry_dispatch = AsyncMock(return_value=_make_outcome())

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset({1}),
        "seen_updates": SeenUpdateTracker(),
        "conversation_manager": MagicMock(),
        "workflow_manager": MagicMock(),
        "analysis_service": MagicMock(),
        "clarification_service": MagicMock(),
        "content_planning_service": MagicMock(),
        "draft_generation_service": MagicMock(),
        "draft_manager": MagicMock(),
        "draft_version_manager": MagicMock(),
        "draft_editing_service": MagicMock(),
        "publication_preparation_service": MagicMock(),
        "execution_service": MagicMock(),
        "execution_dispatch_service": execution_dispatch_service,
        "config": config,
    }

    await handlers.retry(update, context)

    execution_dispatch_service.retry_dispatch.assert_awaited_once_with(publication_id="pub-1", telegram_user_id=1)
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.DISPATCH_STARTED_MESSAGE


# --- /status: dispatch outcomes --------------------------------------------


def _make_execution(**overrides):
    from src.publication.models import PublicationChannel

    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0, publisher="instagram", created_at="t", updated_at="t",
    )
    defaults.update(overrides)
    from src.execution.models import ExecutionDocument

    return ExecutionDocument(**defaults)


def test_describe_execution_state_completed():
    assert handlers._describe_execution_state(_make_execution(status=ExecutionStatus.COMPLETED)) == (
        handlers.STATUS_DISPATCH_COMPLETED_DESCRIPTION
    )


def test_describe_execution_state_in_progress_processing_media():
    execution = _make_execution(status=ExecutionStatus.DISPATCH_IN_PROGRESS, checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING)
    assert handlers._describe_execution_state(execution) == handlers.STATUS_DISPATCH_PROCESSING_MEDIA_DESCRIPTION


def test_describe_execution_state_in_progress_generic():
    execution = _make_execution(status=ExecutionStatus.DISPATCH_IN_PROGRESS, checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED)
    assert handlers._describe_execution_state(execution) == handlers.STATUS_DISPATCH_IN_PROGRESS_DESCRIPTION


def test_describe_execution_state_retryable_failure():
    execution = _make_execution(status=ExecutionStatus.DISPATCH_FAILED, failure={"retryable": True})
    assert handlers._describe_execution_state(execution) == handlers.STATUS_DISPATCH_RETRYABLE_FAILURE_DESCRIPTION


def test_describe_execution_state_permanent_failure():
    execution = _make_execution(status=ExecutionStatus.DISPATCH_FAILED, failure={"retryable": False})
    assert handlers._describe_execution_state(execution) == handlers.STATUS_DISPATCH_PERMANENT_FAILURE_DESCRIPTION


def test_describe_execution_state_never_says_published_before_completed():
    # The COMPLETED-only phrase ("Published to Instagram successfully")
    # must never appear for any non-terminal or failed execution state.
    for status in (ExecutionStatus.READY_FOR_DISPATCH, ExecutionStatus.DISPATCH_IN_PROGRESS, ExecutionStatus.DISPATCH_FAILED):
        execution = _make_execution(status=status, failure={"retryable": True})
        assert handlers.STATUS_DISPATCH_COMPLETED_DESCRIPTION not in handlers._describe_execution_state(execution)
