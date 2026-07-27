from unittest.mock import MagicMock

import pytest

from src.conversation.errors import ConversationNotFoundError, NoActiveWorkflowError, StaleWorkflowPointerError
from src.conversation.resolution import resolve_active_workflow
from src.workflow.errors import WorkflowNotFoundError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


def _make_workflow_document(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def test_resolve_returns_workflow_document_when_pointer_is_valid():
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.return_value = "wf-1"
    workflow_manager = MagicMock()
    document = _make_workflow_document()
    workflow_manager.load_workflow.return_value = document

    result = resolve_active_workflow(42, conversation_manager, workflow_manager)

    assert result is document
    conversation_manager.load_active_workflow.assert_called_once_with(42)
    workflow_manager.load_workflow.assert_called_once_with("wf-1")


def test_resolve_raises_no_active_workflow_when_conversation_missing():
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.side_effect = ConversationNotFoundError("none")
    workflow_manager = MagicMock()

    with pytest.raises(NoActiveWorkflowError):
        resolve_active_workflow(42, conversation_manager, workflow_manager)

    workflow_manager.load_workflow.assert_not_called()


def test_resolve_raises_no_active_workflow_when_pointer_is_none():
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.return_value = None
    workflow_manager = MagicMock()

    with pytest.raises(NoActiveWorkflowError):
        resolve_active_workflow(42, conversation_manager, workflow_manager)

    workflow_manager.load_workflow.assert_not_called()


def test_resolve_raises_stale_pointer_when_workflow_missing():
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.return_value = "wf-missing"
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = WorkflowNotFoundError("missing")

    with pytest.raises(StaleWorkflowPointerError):
        resolve_active_workflow(42, conversation_manager, workflow_manager)


def test_resolve_returns_terminal_workflow_document_without_error():
    # Resolution is state-agnostic: a terminal workflow is still resolved
    # successfully — only /cancel (in handlers.py) treats that specially.
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.return_value = "wf-done"
    workflow_manager = MagicMock()
    document = _make_workflow_document(workflow_id="wf-done", state=WorkflowState.COMPLETED)
    workflow_manager.load_workflow.return_value = document

    result = resolve_active_workflow(42, conversation_manager, workflow_manager)

    assert result.state is WorkflowState.COMPLETED
