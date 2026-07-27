from unittest.mock import MagicMock

import pytest

from src.execution.errors import (
    ExecutionConcurrentModificationError,
    ExecutionDeserializationError,
    ExecutionNotFoundError,
    ExecutionPersistenceError,
    ExecutionSerializationError,
)
from src.execution.models import ExecutionDocument, ExecutionStatus
from src.execution.repository import ExecutionRepository, LoadedExecution
from src.publication.models import PublicationChannel
from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_document(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def test_load_returns_document_and_etag_using_publication_id_as_the_key():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = ExecutionRepository(store)

    result = repository.load("pub-1")

    assert isinstance(result, LoadedExecution)
    assert result.document == document
    assert result.etag == '"etag-1"'
    store.read.assert_called_once_with("pub-1")


def test_load_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionNotFoundError):
        repository.load("pub-1")


def test_load_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionDeserializationError):
        repository.load("pub-1")


def test_load_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("boom")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionPersistenceError):
        repository.load("pub-1")


def test_save_conditional_create_uses_none_etag_and_publication_id_key():
    store = MagicMock()
    store.write.return_value = '"etag-2"'
    repository = ExecutionRepository(store)
    document = _make_document()

    etag = repository.save(document, expected_etag=None)

    assert etag == '"etag-2"'
    store.write.assert_called_once_with("pub-1", document.to_dict(), expected_etag=None)


def test_save_translates_concurrent_modification_error():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionConcurrentModificationError):
        repository.save(_make_document())


def test_save_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionSerializationError):
        repository.save(_make_document())


def test_save_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("boom")
    repository = ExecutionRepository(store)

    with pytest.raises(ExecutionPersistenceError):
        repository.save(_make_document())


def test_exists_delegates_to_store_with_publication_id():
    store = MagicMock()
    store.exists.return_value = True
    repository = ExecutionRepository(store)

    assert repository.exists("pub-1") is True
    store.exists.assert_called_once_with("pub-1")
