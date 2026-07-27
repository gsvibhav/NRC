from unittest.mock import MagicMock

import pytest

from src.conversation.errors import ConversationNotFoundError
from src.conversation.manager import ConversationManager
from src.conversation.models import ConversationDocument
from src.conversation.repository import LoadedConversation
from src.conversation.states import ConversationState


def _make_document(**overrides):
    defaults = dict(
        telegram_user_id=42,
        active_workflow_id="wf-1",
        state=ConversationState.ACTIVE,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ConversationDocument(**defaults)


# --- set_active_workflow -----------------------------------------------------


def test_set_active_workflow_creates_new_conversation_when_none_exists():
    repository = MagicMock()
    repository.load.side_effect = ConversationNotFoundError("none")
    manager = ConversationManager(repository)

    document = manager.set_active_workflow(42, "wf-new")

    assert document.telegram_user_id == 42
    assert document.active_workflow_id == "wf-new"
    assert document.state is ConversationState.ACTIVE

    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert saved_document[0] is document
    assert kwargs["expected_etag"] is None  # conditional create


def test_set_active_workflow_updates_existing_conversation_with_etag():
    existing = _make_document(active_workflow_id="wf-old", state=ConversationState.IDLE)
    repository = MagicMock()
    repository.load.return_value = LoadedConversation(document=existing, etag='"etag-1"')
    manager = ConversationManager(repository)

    document = manager.set_active_workflow(42, "wf-new")

    assert document.active_workflow_id == "wf-new"
    assert document.state is ConversationState.ACTIVE

    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_set_active_workflow_updates_bump_updated_at():
    existing = _make_document(updated_at="2020-01-01T00:00:00+00:00")
    repository = MagicMock()
    repository.load.return_value = LoadedConversation(document=existing, etag='"etag-1"')
    manager = ConversationManager(repository)

    document = manager.set_active_workflow(42, "wf-new")

    assert document.updated_at != "2020-01-01T00:00:00+00:00"


# --- load_active_workflow -----------------------------------------------------


def test_load_active_workflow_returns_pointer_when_active():
    document = _make_document(active_workflow_id="wf-abc", state=ConversationState.ACTIVE)
    repository = MagicMock()
    repository.load.return_value = LoadedConversation(document=document, etag='"etag"')
    manager = ConversationManager(repository)

    result = manager.load_active_workflow(42)

    assert result == "wf-abc"


def test_load_active_workflow_returns_none_when_idle():
    document = _make_document(active_workflow_id=None, state=ConversationState.IDLE)
    repository = MagicMock()
    repository.load.return_value = LoadedConversation(document=document, etag='"etag"')
    manager = ConversationManager(repository)

    result = manager.load_active_workflow(42)

    assert result is None


def test_load_active_workflow_propagates_not_found_error():
    repository = MagicMock()
    repository.load.side_effect = ConversationNotFoundError("none")
    manager = ConversationManager(repository)

    with pytest.raises(ConversationNotFoundError):
        manager.load_active_workflow(42)


# --- clear_active_workflow -----------------------------------------------------


def test_clear_active_workflow_sets_idle_and_clears_pointer():
    document = _make_document(active_workflow_id="wf-abc", state=ConversationState.ACTIVE)
    repository = MagicMock()
    repository.load.return_value = LoadedConversation(document=document, etag='"etag-1"')
    manager = ConversationManager(repository)

    result = manager.clear_active_workflow(42)

    assert result.active_workflow_id is None
    assert result.state is ConversationState.IDLE

    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_clear_active_workflow_propagates_not_found_error():
    repository = MagicMock()
    repository.load.side_effect = ConversationNotFoundError("none")
    manager = ConversationManager(repository)

    with pytest.raises(ConversationNotFoundError):
        manager.clear_active_workflow(42)
