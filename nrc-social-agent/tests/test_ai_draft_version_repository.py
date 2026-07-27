from unittest.mock import MagicMock

import pytest

from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.ai.draft_version_repository import DraftVersionRepository, LoadedCurrentPointer, LoadedDraftVersion
from src.ai.errors import (
    CurrentDraftPointerNotFoundError,
    DraftVersionConflictError,
    DraftVersionDeserializationError,
    DraftVersionNotFoundError,
    DraftVersionPersistenceError,
)
from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=1, prompt_version=1, model="claude-opus-5",
        version_number=1,
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


# --- version records ---------------------------------------------------


def test_load_version_returns_document_and_etag_on_success():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = DraftVersionRepository(store)

    result = repository.load_version("wf-1", "out-1", 1)

    assert isinstance(result, LoadedDraftVersion)
    assert result.document == document
    assert result.etag == '"etag-1"'
    store.read.assert_called_once_with("wf-1/out-1/versions/1")


def test_load_version_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionNotFoundError):
        repository.load_version("wf-1", "out-1", 1)


def test_load_version_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionDeserializationError):
        repository.load_version("wf-1", "out-1", 1)


def test_load_version_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("s3 down")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionPersistenceError):
        repository.load_version("wf-1", "out-1", 1)


def test_version_exists_delegates_to_store_with_composite_key():
    store = MagicMock()
    store.exists.return_value = True
    repository = DraftVersionRepository(store)

    assert repository.version_exists("wf-1", "out-1", 2) is True
    store.exists.assert_called_once_with("wf-1/out-1/versions/2")


def test_save_version_writes_at_the_versioned_key_with_conditional_create():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = DraftVersionRepository(store)
    document = _make_document(version_number=2)

    etag = repository.save_version(document)

    assert etag == '"new-etag"'
    store.write.assert_called_once_with("wf-1/out-1/versions/2", document.to_dict(), expected_etag=None)


def test_save_version_translates_concurrent_modification_to_version_conflict():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("exists")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionConflictError):
        repository.save_version(_make_document())


def test_save_version_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("s3 down")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionPersistenceError):
        repository.save_version(_make_document())


def test_version_key_never_collides_with_current_pointer_key():
    store = MagicMock()
    repository = DraftVersionRepository(store)

    repository.save_version(_make_document(version_number=1))
    version_key = store.write.call_args[0][0]

    repository.save_pointer(_make_pointer())
    pointer_key = store.write.call_args[0][0]

    assert version_key == "wf-1/out-1/versions/1"
    assert pointer_key == "wf-1/out-1/current"
    assert version_key != pointer_key


# --- current pointer -----------------------------------------------------


def test_load_pointer_returns_pointer_and_etag_on_success():
    store = MagicMock()
    pointer = _make_pointer()
    store.read.return_value = (pointer.to_dict(), '"etag-1"')
    repository = DraftVersionRepository(store)

    result = repository.load_pointer("wf-1", "out-1")

    assert isinstance(result, LoadedCurrentPointer)
    assert result.pointer == pointer
    assert result.etag == '"etag-1"'
    store.read.assert_called_once_with("wf-1/out-1/current")


def test_load_pointer_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = DraftVersionRepository(store)

    with pytest.raises(CurrentDraftPointerNotFoundError):
        repository.load_pointer("wf-1", "out-1")


def test_pointer_exists_delegates_to_store():
    store = MagicMock()
    store.exists.return_value = False
    repository = DraftVersionRepository(store)

    assert repository.pointer_exists("wf-1", "out-1") is False
    store.exists.assert_called_once_with("wf-1/out-1/current")


def test_save_pointer_conditional_create():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = DraftVersionRepository(store)

    etag = repository.save_pointer(_make_pointer(), expected_etag=None)

    assert etag == '"new-etag"'
    store.write.assert_called_once_with("wf-1/out-1/current", _make_pointer().to_dict(), expected_etag=None)


def test_save_pointer_conditional_update_with_etag():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = DraftVersionRepository(store)

    repository.save_pointer(_make_pointer(), expected_etag='"old-etag"')

    _, kwargs = store.write.call_args
    assert kwargs["expected_etag"] == '"old-etag"'


def test_save_pointer_translates_concurrent_modification_to_version_conflict():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("stale")
    repository = DraftVersionRepository(store)

    with pytest.raises(DraftVersionConflictError):
        repository.save_pointer(_make_pointer(), expected_etag='"stale"')


def test_save_pointer_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = DraftVersionRepository(store)

    with pytest.raises(Exception):
        repository.save_pointer(_make_pointer())
