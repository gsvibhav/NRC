from unittest.mock import MagicMock

import pytest

from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)
from src.workflow.errors import (
    WorkflowConcurrentModificationError,
    WorkflowDeserializationError,
    WorkflowNotFoundError,
    WorkflowPersistenceError,
    WorkflowSerializationError,
)
from src.workflow.models import WorkflowDocument
from src.workflow.repository import LoadedWorkflow, WorkflowRepository
from src.workflow.states import WorkflowState


def _make_document(**overrides):
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


def test_exists_delegates_to_store():
    store = MagicMock()
    store.exists.return_value = True
    repository = WorkflowRepository(store)

    assert repository.exists("wf-1") is True
    store.exists.assert_called_once_with("wf-1")


def test_exists_wraps_store_errors():
    store = MagicMock()
    store.exists.side_effect = ObjectStoreError("boom")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowPersistenceError):
        repository.exists("wf-1")


def test_load_returns_document_and_etag():
    document = _make_document()
    store = MagicMock()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = WorkflowRepository(store)

    loaded = repository.load("wf-1")

    assert isinstance(loaded, LoadedWorkflow)
    assert loaded.document == document
    assert loaded.etag == '"etag-1"'


def test_load_raises_not_found_error(monkeypatch):
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("missing")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowNotFoundError):
        repository.load("wf-missing")


def test_load_raises_deserialization_error_from_store():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowDeserializationError):
        repository.load("wf-1")


def test_load_raises_deserialization_error_from_invalid_schema():
    # Valid version, but missing every other required field — targets the
    # deserialization-error path specifically, not the version-check path.
    store = MagicMock()
    store.read.return_value = ({"version": 1}, '"etag"')
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowDeserializationError):
        repository.load("wf-1")


def test_load_raises_persistence_error_for_other_store_failures():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("network error")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowPersistenceError):
        repository.load("wf-1")


def test_save_creates_with_no_expected_etag():
    document = _make_document()
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = WorkflowRepository(store)

    etag = repository.save(document, expected_etag=None)

    assert etag == '"new-etag"'
    args, kwargs = store.write.call_args
    assert args[0] == "wf-1"
    assert args[1] == document.to_dict()
    assert kwargs["expected_etag"] is None


def test_save_updates_with_expected_etag():
    document = _make_document()
    store = MagicMock()
    store.write.return_value = '"updated-etag"'
    repository = WorkflowRepository(store)

    repository.save(document, expected_etag='"old-etag"')

    _, kwargs = store.write.call_args
    assert kwargs["expected_etag"] == '"old-etag"'


def test_save_raises_concurrent_modification_error():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowConcurrentModificationError):
        repository.save(document, expected_etag='"stale-etag"')


def test_save_raises_serialization_error():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowSerializationError):
        repository.save(document, expected_etag=None)


def test_save_raises_persistence_error_for_other_failures():
    document = _make_document()
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("boom")
    repository = WorkflowRepository(store)

    with pytest.raises(WorkflowPersistenceError):
        repository.save(document, expected_etag=None)
