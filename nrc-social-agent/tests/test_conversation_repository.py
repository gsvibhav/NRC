from unittest.mock import MagicMock

import pytest

from src.conversation.errors import (
    ConversationConcurrentModificationError,
    ConversationDeserializationError,
    ConversationNotFoundError,
    ConversationPersistenceError,
    ConversationSerializationError,
)
from src.conversation.models import ConversationDocument
from src.conversation.repository import ConversationRepository, LoadedConversation
from src.conversation.states import ConversationState
from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_document(**overrides):
    defaults = dict(
        telegram_user_id=42,
        active_workflow_id="wf-1",
        state=ConversationState.ACTIVE,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ConversationDocument(**defaults)


def test_load_returns_document_and_etag():
    document = _make_document()
    store = MagicMock()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = ConversationRepository(store)

    loaded = repository.load(42)

    assert isinstance(loaded, LoadedConversation)
    assert loaded.document == document
    assert loaded.etag == '"etag-1"'
    store.read.assert_called_once_with("42")


def test_load_raises_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("missing")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationNotFoundError):
        repository.load(42)


def test_load_raises_deserialization_error_from_store():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationDeserializationError):
        repository.load(42)


def test_load_raises_deserialization_error_from_invalid_schema():
    store = MagicMock()
    store.read.return_value = ({"version": 1}, '"etag"')
    repository = ConversationRepository(store)

    with pytest.raises(ConversationDeserializationError):
        repository.load(42)


def test_load_raises_persistence_error_for_other_store_failures():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("network error")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationPersistenceError):
        repository.load(42)


def test_save_creates_with_no_expected_etag():
    document = _make_document()
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = ConversationRepository(store)

    etag = repository.save(document, expected_etag=None)

    assert etag == '"new-etag"'
    args, kwargs = store.write.call_args
    assert args[0] == "42"
    assert args[1] == document.to_dict()
    assert kwargs["expected_etag"] is None


def test_save_updates_with_expected_etag():
    document = _make_document()
    store = MagicMock()
    store.write.return_value = '"updated-etag"'
    repository = ConversationRepository(store)

    repository.save(document, expected_etag='"old-etag"')

    _, kwargs = store.write.call_args
    assert kwargs["expected_etag"] == '"old-etag"'


def test_save_raises_concurrent_modification_error():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationConcurrentModificationError):
        repository.save(document, expected_etag='"stale-etag"')


def test_save_raises_serialization_error():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationSerializationError):
        repository.save(document, expected_etag=None)


def test_save_raises_persistence_error_for_other_failures():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("boom")
    repository = ConversationRepository(store)

    with pytest.raises(ConversationPersistenceError):
        repository.save(document, expected_etag=None)
