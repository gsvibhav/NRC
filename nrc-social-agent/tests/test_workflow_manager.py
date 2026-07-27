from unittest.mock import MagicMock

import pytest

from src.workflow.errors import InvalidWorkflowStateError, WorkflowConcurrentModificationError
from src.workflow.manager import WorkflowManager
from src.workflow.models import WorkflowDocument
from src.workflow.repository import LoadedWorkflow
from src.workflow.states import WorkflowState


def _make_document(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={"s3_key": "media/42/wf-1/original/file.jpg"},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


# --- create_workflow -------------------------------------------------------


def test_create_workflow_persists_with_conditional_create():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    document = manager.create_workflow(
        workflow_id="wf-1", telegram_user_id=42, media={"s3_key": "x"}
    )

    assert document.workflow_id == "wf-1"
    assert document.telegram_user_id == 42
    assert document.media == {"s3_key": "x"}

    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert saved_document[0] is document
    assert kwargs["expected_etag"] is None  # conditional create, never overwrite


def test_create_workflow_defaults_to_analyzing_media_state():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    document = manager.create_workflow(workflow_id="wf-1", telegram_user_id=1, media={})

    assert document.state is WorkflowState.ANALYZING_MEDIA


def test_create_workflow_sets_created_at_equal_to_updated_at():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    document = manager.create_workflow(workflow_id="wf-1", telegram_user_id=1, media={})

    assert document.created_at == document.updated_at


def test_create_workflow_accepts_explicit_initial_state():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    document = manager.create_workflow(
        workflow_id="wf-1", telegram_user_id=1, media={}, initial_state=WorkflowState.FAILED
    )

    assert document.state is WorkflowState.FAILED


def test_create_workflow_rejects_non_workflow_state_initial_state():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    with pytest.raises(InvalidWorkflowStateError):
        manager.create_workflow(
            workflow_id="wf-1", telegram_user_id=1, media={}, initial_state="NOT_A_STATE"
        )

    repository.save.assert_not_called()


def test_create_workflow_propagates_repository_errors():
    repository = MagicMock()
    repository.save.side_effect = WorkflowConcurrentModificationError("workflow_id already exists")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.create_workflow(workflow_id="wf-1", telegram_user_id=1, media={})


# --- load_workflow ----------------------------------------------------------


def test_load_workflow_returns_document_from_repository():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    result = manager.load_workflow("wf-1")

    assert result == document
    repository.load.assert_called_once_with("wf-1")


# --- update_state ------------------------------------------------------------


def test_update_state_saves_with_etag_from_load():
    document = _make_document(state=WorkflowState.ANALYZING_MEDIA)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_state("wf-1", WorkflowState.WAITING_FOR_USER)

    assert updated.state is WorkflowState.WAITING_FOR_USER
    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert saved_document[0].state is WorkflowState.WAITING_FOR_USER
    assert kwargs["expected_etag"] == '"etag-1"'


def test_update_state_bumps_updated_at_but_preserves_created_at():
    document = _make_document(created_at="2020-01-01T00:00:00+00:00", updated_at="2020-01-01T00:00:00+00:00")
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_state("wf-1", WorkflowState.COMPLETED)

    assert updated.created_at == "2020-01-01T00:00:00+00:00"
    assert updated.updated_at != "2020-01-01T00:00:00+00:00"


def test_update_state_preserves_other_fields():
    document = _make_document(conversation=[{"a": 1}], metadata={"k": "v"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_state("wf-1", WorkflowState.REJECTED)

    assert updated.conversation == [{"a": 1}]
    assert updated.metadata == {"k": "v"}
    assert updated.workflow_id == document.workflow_id


def test_update_state_rejects_non_workflow_state_value():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    with pytest.raises(InvalidWorkflowStateError):
        manager.update_state("wf-1", "NOT_A_STATE")

    repository.load.assert_not_called()
    repository.save.assert_not_called()


def test_update_state_propagates_concurrent_modification_error():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"stale-etag"')
    repository.save.side_effect = WorkflowConcurrentModificationError("changed since load")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.update_state("wf-1", WorkflowState.COMPLETED)


# --- record_user_reply (Milestone 4B) ---------------------------------


def test_record_user_reply_appends_turn_clears_pending_question_and_reenters_analyzing():
    document = _make_document(
        state=WorkflowState.WAITING_FOR_USER,
        pending_question={"question_id": "q1", "text": "Which platform?"},
        conversation=[{"turn_id": "t1", "role": "assistant", "type": "clarification_question"}],
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    turn = {"turn_id": "t2", "role": "user", "type": "clarification_answer", "content": "NRC"}
    updated = manager.record_user_reply("wf-1", turn=turn)

    assert updated.conversation == [document.conversation[0], turn]
    assert updated.pending_question is None
    assert updated.state is WorkflowState.ANALYZING_MEDIA
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_record_user_reply_does_not_mutate_prior_conversation_list():
    original_turns = [{"turn_id": "t1"}]
    document = _make_document(state=WorkflowState.WAITING_FOR_USER, conversation=original_turns)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    manager.record_user_reply("wf-1", turn={"turn_id": "t2"})

    assert original_turns == [{"turn_id": "t1"}]  # unchanged in place


# --- apply_clarification_decision (Milestone 4B) -----------------------


def test_apply_clarification_decision_persists_question_and_transitions_to_waiting_for_user():
    document = _make_document(state=WorkflowState.ANALYZING_MEDIA)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    question_turn = {"turn_id": "t1", "role": "assistant", "type": "clarification_question", "content": "Which platform?"}
    pending_question = {"question_id": "q1", "text": "Which platform?"}

    updated = manager.apply_clarification_decision(
        "wf-1",
        clarification_context={"version": 1},
        new_state=WorkflowState.WAITING_FOR_USER,
        question_turn=question_turn,
        pending_question=pending_question,
        metadata_updates={"pending_retry": None},
    )

    assert updated.state is WorkflowState.WAITING_FOR_USER
    assert updated.conversation == [question_turn]
    assert updated.pending_question == pending_question
    assert updated.clarification_context == {"version": 1}
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_apply_clarification_decision_transitions_to_generating_content_without_a_question():
    document = _make_document(state=WorkflowState.ANALYZING_MEDIA)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.apply_clarification_decision(
        "wf-1",
        clarification_context={"version": 1, "objective": "intro"},
        new_state=WorkflowState.GENERATING_CONTENT,
    )

    assert updated.state is WorkflowState.GENERATING_CONTENT
    assert updated.conversation == []
    assert updated.pending_question is None


def test_apply_clarification_decision_merges_metadata_updates_and_removes_none_values():
    document = _make_document(metadata={"pending_retry": "clarification_decision", "keep": "me"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.apply_clarification_decision(
        "wf-1",
        clarification_context={},
        new_state=WorkflowState.GENERATING_CONTENT,
        metadata_updates={"pending_retry": None, "new_key": "value"},
    )

    assert updated.metadata == {"keep": "me", "new_key": "value"}


def test_apply_clarification_decision_rejects_invalid_state():
    repository = MagicMock()
    manager = WorkflowManager(repository)

    with pytest.raises(InvalidWorkflowStateError):
        manager.apply_clarification_decision("wf-1", clarification_context={}, new_state="NOT_A_STATE")

    repository.load.assert_not_called()


def test_apply_clarification_decision_propagates_concurrent_modification_error():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"stale"')
    repository.save.side_effect = WorkflowConcurrentModificationError("changed since load")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.apply_clarification_decision("wf-1", clarification_context={}, new_state=WorkflowState.GENERATING_CONTENT)


# --- update_metadata (Milestone 4B) -------------------------------------


def test_update_metadata_merges_new_keys():
    document = _make_document(metadata={"existing": "value"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_metadata("wf-1", {"pending_retry": "analysis"})

    assert updated.metadata == {"existing": "value", "pending_retry": "analysis"}


def test_update_metadata_removes_key_when_value_is_none():
    document = _make_document(metadata={"pending_retry": "analysis", "other": "value"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_metadata("wf-1", {"pending_retry": None})

    assert updated.metadata == {"other": "value"}


def test_update_metadata_removing_absent_key_is_a_no_op():
    document = _make_document(metadata={"other": "value"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.update_metadata("wf-1", {"pending_retry": None})

    assert updated.metadata == {"other": "value"}


# --- attach_generated_draft_reference (Milestone 6) ----------------------


def test_attach_generated_draft_reference_saves_reference_and_transitions_state():
    document = _make_document(state=WorkflowState.GENERATING_CONTENT)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"draft_id": "d1", "output_id": "out-1", "status": "READY_FOR_REVIEW"}

    updated = manager.attach_generated_draft_reference(
        "wf-1", reference, new_state=WorkflowState.SHOWING_PREVIEW
    )

    assert updated.generated_draft == reference
    assert updated.state is WorkflowState.SHOWING_PREVIEW
    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert saved_document[0].generated_draft == reference
    assert saved_document[0].state is WorkflowState.SHOWING_PREVIEW
    assert kwargs["expected_etag"] == '"etag-1"'


def test_attach_generated_draft_reference_rejects_non_workflow_state():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    with pytest.raises(InvalidWorkflowStateError):
        manager.attach_generated_draft_reference("wf-1", {}, new_state="not-a-state")

    repository.save.assert_not_called()


def test_attach_generated_draft_reference_preserves_other_fields():
    document = _make_document(
        state=WorkflowState.GENERATING_CONTENT, content_plan={"plan_id": "p1"}, conversation=[{"a": 1}]
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.attach_generated_draft_reference(
        "wf-1", {"draft_id": "d1"}, new_state=WorkflowState.SHOWING_PREVIEW
    )

    assert updated.content_plan == {"plan_id": "p1"}
    assert updated.conversation == [{"a": 1}]


def test_attach_generated_draft_reference_propagates_concurrent_modification_error():
    document = _make_document(state=WorkflowState.GENERATING_CONTENT)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    repository.save.side_effect = WorkflowConcurrentModificationError("conflict")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.attach_generated_draft_reference(
            "wf-1", {"draft_id": "d1"}, new_state=WorkflowState.SHOWING_PREVIEW
        )


# --- Milestone 7: editing lifecycle --------------------------------------


def test_enter_editing_transitions_to_editing_and_sets_pending_edit():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    pending_edit = {"output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1, "status": "AWAITING_INSTRUCTION"}

    updated = manager.enter_editing("wf-1", pending_edit=pending_edit)

    assert updated.state is WorkflowState.EDITING
    assert updated.pending_edit == pending_edit
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_record_edit_instruction_appends_turn_updates_pending_edit_and_transitions():
    document = _make_document(
        state=WorkflowState.EDITING, conversation=[{"a": 1}],
        pending_edit={"output_id": "out-1", "operation_id": "op-1", "status": "AWAITING_INSTRUCTION"},
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    turn = {"role": "user", "content": "Make it shorter.", "turn_id": "op-1"}
    updated_pending_edit = {"output_id": "out-1", "operation_id": "op-1", "status": "INSTRUCTION_RECEIVED"}

    updated = manager.record_edit_instruction("wf-1", turn=turn, pending_edit=updated_pending_edit)

    assert updated.state is WorkflowState.GENERATING_CONTENT
    assert updated.conversation == [{"a": 1}, turn]
    assert updated.pending_edit == updated_pending_edit


def test_record_edit_instruction_does_not_mutate_prior_conversation_list():
    original_conversation = [{"a": 1}]
    document = _make_document(state=WorkflowState.EDITING, conversation=original_conversation)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    manager.record_edit_instruction("wf-1", turn={"role": "user", "content": "x"}, pending_edit={})

    assert original_conversation == [{"a": 1}]


def test_complete_edit_updates_reference_clears_retry_and_transitions_to_showing_preview():
    document = _make_document(
        state=WorkflowState.GENERATING_CONTENT, metadata={"pending_retry": "draft_editing"},
        pending_edit={"status": "INSTRUCTION_RECEIVED"},
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"draft_id": "d2", "current_version": 2, "status": "READY_FOR_REVIEW"}
    completed_pending_edit = {"status": "COMPLETED", "completed_version_number": 2}

    updated = manager.complete_edit(
        "wf-1", generated_draft_reference=reference, pending_edit=completed_pending_edit
    )

    assert updated.state is WorkflowState.SHOWING_PREVIEW
    assert updated.generated_draft == reference
    assert updated.pending_edit == completed_pending_edit
    assert "pending_retry" not in updated.metadata


def test_fail_edit_to_preview_clears_pending_edit_and_retry_returns_to_showing_preview():
    document = _make_document(
        state=WorkflowState.GENERATING_CONTENT, metadata={"pending_retry": "draft_editing"},
        pending_edit={"status": "INSTRUCTION_RECEIVED"}, generated_draft={"draft_id": "d1", "current_version": 1},
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.fail_edit_to_preview("wf-1")

    assert updated.state is WorkflowState.SHOWING_PREVIEW
    assert updated.pending_edit is None
    assert "pending_retry" not in updated.metadata
    # the current draft reference is untouched -- no new version was created
    assert updated.generated_draft == {"draft_id": "d1", "current_version": 1}


def test_set_edit_pending_retry_leaves_state_and_pending_edit_untouched():
    document = _make_document(
        state=WorkflowState.GENERATING_CONTENT, pending_edit={"status": "INSTRUCTION_RECEIVED", "operation_id": "op-1"},
    )
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.set_edit_pending_retry("wf-1")

    assert updated.state is WorkflowState.GENERATING_CONTENT
    assert updated.pending_edit == {"status": "INSTRUCTION_RECEIVED", "operation_id": "op-1"}
    assert updated.metadata["pending_retry"] == "draft_editing"


# --- Milestone 7: approve / save / reject ---------------------------------


def test_approve_draft_transitions_directly_to_completed():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW, pending_edit={"status": "COMPLETED"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"draft_id": "d1", "status": "APPROVED", "current_version": 1}

    updated = manager.approve_draft("wf-1", generated_draft_reference=reference)

    assert updated.state is WorkflowState.COMPLETED
    assert updated.generated_draft == reference
    assert updated.pending_edit is None


def test_approve_draft_clears_pending_retry():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW, metadata={"pending_retry": "draft_editing"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.approve_draft("wf-1", generated_draft_reference={"status": "APPROVED"})

    assert "pending_retry" not in updated.metadata


def test_save_draft_transitions_to_saved_as_draft():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"draft_id": "d1", "status": "SAVED_AS_DRAFT", "current_version": 1}

    updated = manager.save_draft("wf-1", generated_draft_reference=reference)

    assert updated.state is WorkflowState.SAVED_AS_DRAFT
    assert updated.generated_draft == reference


def test_reject_draft_transitions_to_rejected():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"draft_id": "d1", "status": "REJECTED", "current_version": 1}

    updated = manager.reject_draft("wf-1", generated_draft_reference=reference)

    assert updated.state is WorkflowState.REJECTED
    assert updated.generated_draft == reference


def test_approve_save_reject_propagate_concurrent_modification_error():
    document = _make_document(state=WorkflowState.SHOWING_PREVIEW)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    repository.save.side_effect = WorkflowConcurrentModificationError("conflict")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.approve_draft("wf-1", generated_draft_reference={})
    with pytest.raises(WorkflowConcurrentModificationError):
        manager.save_draft("wf-1", generated_draft_reference={})
    with pytest.raises(WorkflowConcurrentModificationError):
        manager.reject_draft("wf-1", generated_draft_reference={})


# --- Milestone 8: attach_publication_reference ---------------------------


def test_attach_publication_reference_saves_reference_without_changing_state():
    document = _make_document(state=WorkflowState.COMPLETED)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)
    reference = {"publication_id": "pub-1", "status": "READY_FOR_PUBLISHING"}

    updated = manager.attach_publication_reference("wf-1", reference)

    assert updated.publication == reference
    assert updated.state is WorkflowState.COMPLETED
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_attach_publication_reference_can_clear_the_reference():
    document = _make_document(state=WorkflowState.COMPLETED, publication={"publication_id": "pub-1"})
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    manager = WorkflowManager(repository)

    updated = manager.attach_publication_reference("wf-1", None)

    assert updated.publication is None


def test_attach_publication_reference_propagates_concurrent_modification_error():
    document = _make_document(state=WorkflowState.COMPLETED)
    repository = MagicMock()
    repository.load.return_value = LoadedWorkflow(document=document, etag='"etag-1"')
    repository.save.side_effect = WorkflowConcurrentModificationError("conflict")
    manager = WorkflowManager(repository)

    with pytest.raises(WorkflowConcurrentModificationError):
        manager.attach_publication_reference("wf-1", {"publication_id": "pub-1"})
