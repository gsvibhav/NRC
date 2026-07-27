from unittest.mock import MagicMock

import pytest

from src.conversation.errors import ConversationPersistenceError
from src.conversation.lifecycle import clear_pointer_if_terminal
from src.workflow.states import WorkflowState


@pytest.mark.parametrize(
    "state",
    [WorkflowState.COMPLETED, WorkflowState.REJECTED, WorkflowState.SAVED_AS_DRAFT, WorkflowState.FAILED],
)
def test_clear_pointer_if_terminal_clears_for_every_terminal_state(state):
    conversation_manager = MagicMock()

    clear_pointer_if_terminal(telegram_user_id=1, workflow_state=state, conversation_manager=conversation_manager)

    conversation_manager.clear_active_workflow.assert_called_once_with(1)


@pytest.mark.parametrize(
    "state",
    [
        WorkflowState.IDLE,
        WorkflowState.RECEIVING_MEDIA,
        WorkflowState.UPLOADING_MEDIA,
        WorkflowState.ANALYZING_MEDIA,
        WorkflowState.WAITING_FOR_USER,
        WorkflowState.GENERATING_CONTENT,
    ],
)
def test_clear_pointer_if_terminal_is_a_no_op_for_non_terminal_states(state):
    conversation_manager = MagicMock()

    clear_pointer_if_terminal(telegram_user_id=1, workflow_state=state, conversation_manager=conversation_manager)

    conversation_manager.clear_active_workflow.assert_not_called()


def test_clear_pointer_if_terminal_never_raises_on_conversation_error():
    conversation_manager = MagicMock()
    conversation_manager.clear_active_workflow.side_effect = ConversationPersistenceError("s3 down")

    clear_pointer_if_terminal(
        telegram_user_id=1, workflow_state=WorkflowState.FAILED, conversation_manager=conversation_manager
    )  # must not raise
