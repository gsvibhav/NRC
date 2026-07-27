from unittest.mock import AsyncMock, MagicMock

import pytest

from src import handlers
from src.ai.draft_editing_service import DraftEditOutcome
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.ai.errors import DraftEditValidationFailedError
from src.conversation.errors import NoActiveWorkflowError
from src.utils.dedup import SeenUpdateTracker
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


def _make_current_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=1, prompt_version=1, model="claude-opus-5",
        content={"caption": "A great caption.", "hashtags": [], "cta": None}, version_number=1,
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def _make_pointer(**overrides):
    defaults = dict(
        workflow_id="wf-1", output_id="out-1", current_draft_id="draft-1", current_version_number=1,
        status=ReviewStatus.READY_FOR_REVIEW, updated_at="x",
    )
    defaults.update(overrides)
    return CurrentDraftPointer(**defaults)


def _make_workflow_document(**overrides):
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=1, created_at="x", updated_at="x",
        state=WorkflowState.SHOWING_PREVIEW, media={}, generated_draft={"output_id": "out-1"},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_callback_update_and_context(user_id, allowed_ids, *, callback_data, update_id=1):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.callback_query = query
    update.effective_message = query.message

    draft_version_manager = MagicMock()
    draft_version_manager.get_current.return_value = (_make_current_document(), _make_pointer(), '"etag-1"')

    publication_preparation_service = MagicMock()
    publication_preparation_service.prepare_publication = AsyncMock(return_value=MagicMock())

    execution_service = MagicMock()
    execution_service.create_execution = AsyncMock(return_value=MagicMock())

    config = MagicMock()
    config.instagram_publishing_enabled = False

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "conversation_manager": MagicMock(),
        "workflow_manager": MagicMock(),
        "draft_manager": MagicMock(),
        "draft_version_manager": draft_version_manager,
        "publication_preparation_service": publication_preparation_service,
        "execution_service": execution_service,
        "execution_dispatch_service": MagicMock(),
        "config": config,
    }
    return update, context, query


# --- authorization / malformed / resolution --------------------------------


async def test_review_action_denies_unauthorized_user():
    update, context, query = _make_callback_update_and_context(999, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with("Sorry, you're not authorized to use this bot.")


async def test_review_action_rejects_malformed_callback_data(monkeypatch):
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:publish:wf-1:1")

    await handlers.review_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()


async def test_review_action_reports_no_active_workflow(monkeypatch):
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(side_effect=NoActiveWorkflowError("none")))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.REVIEW_ACTION_NO_ACTIVE_WORKFLOW_MESSAGE)


async def test_review_action_rejects_foreign_workflow_reference(monkeypatch):
    document = _make_workflow_document(workflow_id="wf-other")
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.STALE_ACTION_MESSAGE)
    context.bot_data["workflow_manager"].approve_draft.assert_not_called()


async def test_review_action_skips_duplicate_update(monkeypatch):
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1", update_id=42)
    context.bot_data["seen_updates"].seen_before(42)

    await handlers.review_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()


# --- approve -----------------------------------------------------------


async def test_review_action_approve_success(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["draft_version_manager"].update_pointer_status.assert_called_once()
    _, kwargs = context.bot_data["draft_version_manager"].update_pointer_status.call_args
    assert kwargs["status"] is ReviewStatus.APPROVED
    assert kwargs["approved_by_telegram_user_id"] == 1

    context.bot_data["workflow_manager"].approve_draft.assert_called_once()
    _, kwargs = context.bot_data["workflow_manager"].approve_draft.call_args
    assert kwargs["generated_draft_reference"]["status"] == "APPROVED"

    # Approval chains directly into publication preparation (Milestone 8)
    # and then execution-record creation (Milestone 9) — the active
    # pointer is NOT cleared until that whole chain resolves; ownership
    # moved into the services themselves.
    context.bot_data["publication_preparation_service"].prepare_publication.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=False,
    )
    context.bot_data["execution_service"].create_execution.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=True,
    )
    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


async def test_review_action_approve_repeat_is_idempotent(monkeypatch):
    document = _make_workflow_document(state=WorkflowState.COMPLETED, publication={"status": "READY_FOR_PUBLISHING"})
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    context.bot_data["draft_version_manager"].get_current.return_value = (
        _make_current_document(), _make_pointer(status=ReviewStatus.APPROVED), '"etag-1"',
    )

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)
    context.bot_data["workflow_manager"].approve_draft.assert_not_called()
    context.bot_data["publication_preparation_service"].prepare_publication.assert_not_called()


async def test_review_action_approve_rejects_stale_version_and_resends_preview(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    context.bot_data["draft_version_manager"].get_current.return_value = (
        _make_current_document(version_number=2), _make_pointer(current_version_number=2), '"etag-1"',
    )

    await handlers.review_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.STALE_ACTION_MESSAGE
    assert "Version 2" in calls[1].args[0]
    context.bot_data["workflow_manager"].approve_draft.assert_not_called()


async def test_review_action_approve_rejects_wrong_state(monkeypatch):
    document = _make_workflow_document(state=WorkflowState.EDITING)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    assert query.message.reply_text.await_args_list[0].args[0] == handlers.STALE_ACTION_MESSAGE
    context.bot_data["workflow_manager"].approve_draft.assert_not_called()


# --- save draft --------------------------------------------------------


async def test_review_action_save_success(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:save:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["draft_version_manager"].update_pointer_status.assert_called_once()
    _, kwargs = context.bot_data["draft_version_manager"].update_pointer_status.call_args
    assert kwargs["status"] is ReviewStatus.SAVED_AS_DRAFT

    context.bot_data["workflow_manager"].save_draft.assert_called_once()
    query.message.reply_text.assert_awaited_once_with(handlers.SAVE_DRAFT_SUCCESS_MESSAGE)


async def test_review_action_save_repeat_is_idempotent(monkeypatch):
    document = _make_workflow_document(state=WorkflowState.SAVED_AS_DRAFT)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:save:wf-1:1")
    context.bot_data["draft_version_manager"].get_current.return_value = (
        _make_current_document(), _make_pointer(status=ReviewStatus.SAVED_AS_DRAFT), '"etag-1"',
    )

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.SAVE_DRAFT_SUCCESS_MESSAGE)
    context.bot_data["workflow_manager"].save_draft.assert_not_called()


# --- reject --------------------------------------------------------------


async def test_review_action_reject_success(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:reject:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["draft_version_manager"].update_pointer_status.assert_called_once()
    _, kwargs = context.bot_data["draft_version_manager"].update_pointer_status.call_args
    assert kwargs["status"] is ReviewStatus.REJECTED

    context.bot_data["workflow_manager"].reject_draft.assert_called_once()
    query.message.reply_text.assert_awaited_once_with(handlers.REJECT_SUCCESS_MESSAGE)


async def test_review_action_reject_repeat_is_idempotent(monkeypatch):
    document = _make_workflow_document(state=WorkflowState.REJECTED)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:reject:wf-1:1")
    context.bot_data["draft_version_manager"].get_current.return_value = (
        _make_current_document(), _make_pointer(status=ReviewStatus.REJECTED), '"etag-1"',
    )

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.REJECT_SUCCESS_MESSAGE)
    context.bot_data["workflow_manager"].reject_draft.assert_not_called()


# --- edit entry ----------------------------------------------------------


async def test_review_action_edit_entry_success(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:edit:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["workflow_manager"].enter_editing.assert_called_once()
    _, kwargs = context.bot_data["workflow_manager"].enter_editing.call_args
    assert kwargs["pending_edit"]["expected_parent_version"] == 1
    assert kwargs["pending_edit"]["status"] == "AWAITING_INSTRUCTION"
    query.message.reply_text.assert_awaited_once_with(handlers.EDIT_MODE_PROMPT_MESSAGE)


async def test_review_action_edit_entry_duplicate_delivery_reprompts_without_reentering(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.EDITING,
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "AWAITING_INSTRUCTION"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:edit:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["workflow_manager"].enter_editing.assert_not_called()
    query.message.reply_text.assert_awaited_once_with(handlers.EDIT_MODE_PROMPT_MESSAGE)


async def test_review_action_edit_entry_rejects_stale_version(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:edit:wf-1:1")
    context.bot_data["draft_version_manager"].get_current.return_value = (
        _make_current_document(version_number=2), _make_pointer(current_version_number=2), '"etag-1"',
    )

    await handlers.review_action(update, context)

    assert query.message.reply_text.await_args_list[0].args[0] == handlers.STALE_ACTION_MESSAGE
    context.bot_data["workflow_manager"].enter_editing.assert_not_called()


async def test_review_action_edit_entry_no_output_id_reports_unavailable(monkeypatch):
    document = _make_workflow_document(generated_draft=None)
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:edit:wf-1:1")

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.REVIEW_ACTION_UNAVAILABLE_MESSAGE)


# --- text_reply(): EDITING branch -----------------------------------------


def _make_text_update_and_context(user_id, allowed_ids, *, text, update_id=1):
    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.effective_message.reply_text = AsyncMock()
    update.effective_message.text = text

    draft_editing_service = MagicMock()
    draft_editing_service.edit_draft = AsyncMock(
        return_value=DraftEditOutcome(
            workflow_id="wf-1", draft_id="draft-2", output_id="out-1", output_type="instagram_reel_caption",
            version_number=2, content={"caption": "A revised caption.", "hashtags": [], "cta": None},
        )
    )

    config = MagicMock()
    config.instagram_publishing_enabled = False

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "conversation_manager": MagicMock(),
        "workflow_manager": MagicMock(),
        "analysis_service": MagicMock(),
        "clarification_service": MagicMock(),
        "content_planning_service": MagicMock(),
        "draft_generation_service": MagicMock(),
        "draft_manager": MagicMock(),
        "draft_version_manager": MagicMock(),
        "draft_editing_service": draft_editing_service,
        "publication_preparation_service": MagicMock(),
        "execution_service": MagicMock(),
        "execution_dispatch_service": MagicMock(),
        "config": config,
    }
    return update, context


async def test_text_reply_in_editing_state_persists_instruction_and_runs_edit(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.EDITING,
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "AWAITING_INSTRUCTION"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text="Make it shorter.")

    await handlers.text_reply(update, context)

    context.bot_data["workflow_manager"].record_edit_instruction.assert_called_once()
    _, kwargs = context.bot_data["workflow_manager"].record_edit_instruction.call_args
    assert kwargs["turn"]["content"] == "Make it shorter."
    assert kwargs["turn"]["turn_id"] == "op-1"
    assert kwargs["pending_edit"]["status"] == "INSTRUCTION_RECEIVED"

    context.bot_data["draft_editing_service"].edit_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.EDIT_PROCESSING_MESSAGE
    assert "A revised caption." in calls[1].args[0]


async def test_text_reply_in_editing_state_does_not_treat_as_clarification_reply(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.EDITING,
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "AWAITING_INSTRUCTION"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text="Make it shorter.")

    await handlers.text_reply(update, context)

    context.bot_data["clarification_service"].handle_user_reply.assert_not_called()


async def test_text_reply_in_editing_state_replies_generically_on_edit_failure(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.EDITING,
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "AWAITING_INSTRUCTION"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text="Turn this into a LinkedIn post.")
    error = DraftEditValidationFailedError("too generic")
    context.bot_data["draft_editing_service"].edit_draft = AsyncMock(side_effect=error)

    await handlers.text_reply(update, context)

    _, second_call = update.effective_message.reply_text.await_args_list
    assert second_call.args[0] == error.user_message
    assert "too generic" not in second_call.args[0]


# --- /retry: draft_editing --------------------------------------------------


async def test_retry_reattempts_draft_editing_only(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.GENERATING_CONTENT, metadata={"pending_retry": "draft_editing"},
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "INSTRUCTION_RECEIVED"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text=None)

    await handlers.retry(update, context)

    context.bot_data["draft_editing_service"].edit_draft.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1
    )
    calls = update.effective_message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.EDIT_PROCESSING_MESSAGE


# --- /retry: publication_preparation --------------------------------------


async def test_retry_reattempts_publication_preparation_only(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.COMPLETED, metadata={"pending_retry": "publication_preparation"},
        generated_draft={"output_id": "out-1", "status": "APPROVED"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text=None)
    context.bot_data["publication_preparation_service"].prepare_publication = AsyncMock(return_value=MagicMock())
    context.bot_data["execution_service"].create_execution = AsyncMock(return_value=MagicMock())

    await handlers.retry(update, context)

    context.bot_data["publication_preparation_service"].prepare_publication.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=False,
    )
    context.bot_data["execution_service"].create_execution.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=True,
    )
    context.bot_data["draft_editing_service"].edit_draft.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


# --- Milestone 9: execution creation chained after preparation ------------


async def test_review_action_approve_success_chains_execution_creation(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")

    await handlers.review_action(update, context)

    context.bot_data["execution_service"].create_execution.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=True,
    )
    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


async def test_review_action_approve_success_still_replies_prepared_when_execution_creation_fails_permanently(monkeypatch):
    from src.execution.errors import PublicationPackageNotReadyForExecutionError

    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    context.bot_data["execution_service"].create_execution = AsyncMock(
        side_effect=PublicationPackageNotReadyForExecutionError("unreachable in practice")
    )

    await handlers.review_action(update, context)

    # The package genuinely is prepared regardless of how execution-record
    # creation goes — this milestone never surfaces that failure distinctly.
    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


async def test_review_action_approve_success_still_replies_prepared_when_execution_creation_fails_retryably(monkeypatch):
    from src.execution.errors import ExecutionPersistenceError

    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    context.bot_data["execution_service"].create_execution = AsyncMock(side_effect=ExecutionPersistenceError("S3 hiccup"))

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


# --- /retry: publication_execution -----------------------------------------


async def test_retry_reattempts_publication_execution_only(monkeypatch):
    document = _make_workflow_document(
        state=WorkflowState.COMPLETED, metadata={"pending_retry": "publication_execution"},
        publication={"publication_id": "pub-1", "output_id": "out-1", "status": "READY_FOR_PUBLISHING"},
    )
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context = _make_text_update_and_context(1, {1}, text=None)
    context.bot_data["execution_service"].create_execution = AsyncMock(return_value=MagicMock())

    await handlers.retry(update, context)

    context.bot_data["execution_service"].create_execution.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=True,
    )
    context.bot_data["publication_preparation_service"].prepare_publication.assert_not_called()
    context.bot_data["draft_editing_service"].edit_draft.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)


# --- Milestone 10: "Publish to Instagram" button attachment --------------


async def test_review_action_approve_success_attaches_publish_button_when_enabled(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    context.bot_data["config"].instagram_publishing_enabled = True
    outcome = MagicMock()
    outcome.publication_id = "pub-1"
    context.bot_data["execution_service"].create_execution = AsyncMock(return_value=outcome)

    await handlers.review_action(update, context)

    context.bot_data["execution_service"].create_execution.assert_awaited_once_with(
        workflow_id="wf-1", telegram_user_id=1, clear_pointer_on_success=False,
    )
    _, kwargs = query.message.reply_text.call_args
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "dispatch:instagram:pub-1"


async def test_review_action_approve_success_omits_publish_button_when_disabled(monkeypatch):
    document = _make_workflow_document()
    monkeypatch.setattr(handlers, "resolve_active_workflow", MagicMock(return_value=document))
    update, context, query = _make_callback_update_and_context(1, {1}, callback_data="review:approve:wf-1:1")
    # config.instagram_publishing_enabled is False by default in this fixture

    await handlers.review_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.APPROVE_AND_PREPARED_MESSAGE)
