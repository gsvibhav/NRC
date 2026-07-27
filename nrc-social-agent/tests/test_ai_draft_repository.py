from unittest.mock import MagicMock

import pytest

from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_repository import DraftRepository, LoadedDraft
from src.ai.errors import (
    DraftConcurrentModificationError,
    DraftDeserializationError,
    DraftNotFoundError,
    DraftPersistenceError,
    DraftSerializationError,
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
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def test_load_returns_document_and_etag_on_success():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = DraftRepository(store)

    result = repository.load("wf-1", "out-1")

    assert isinstance(result, LoadedDraft)
    assert result.document == document
    assert result.etag == '"etag-1"'
    store.read.assert_called_once_with("wf-1/out-1")


def test_load_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = DraftRepository(store)

    with pytest.raises(DraftNotFoundError):
        repository.load("wf-1", "out-1")


def test_load_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = DraftRepository(store)

    with pytest.raises(DraftDeserializationError):
        repository.load("wf-1", "out-1")


def test_load_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("s3 down")
    repository = DraftRepository(store)

    with pytest.raises(DraftPersistenceError):
        repository.load("wf-1", "out-1")


def test_exists_delegates_to_store_with_composite_key():
    store = MagicMock()
    store.exists.return_value = True
    repository = DraftRepository(store)

    assert repository.exists("wf-1", "out-1") is True
    store.exists.assert_called_once_with("wf-1/out-1")


def test_exists_translates_generic_store_error():
    store = MagicMock()
    store.exists.side_effect = ObjectStoreError("s3 down")
    repository = DraftRepository(store)

    with pytest.raises(DraftPersistenceError):
        repository.exists("wf-1", "out-1")


def test_save_writes_document_dict_at_the_composite_key_and_returns_etag():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = DraftRepository(store)
    document = _make_document()

    etag = repository.save(document, expected_etag=None)

    assert etag == '"new-etag"'
    store.write.assert_called_once_with("wf-1/out-1", document.to_dict(), expected_etag=None)


def test_save_never_collides_with_the_reserved_workflow_level_draft_key():
    # drafts/<workflow_id>/<output_id>.json (this milestone) must never
    # collide with drafts/<workflow_id>.json (the reserved Milestone 3
    # "Save Draft" key) — the composite key always contains a literal "/".
    store = MagicMock()
    repository = DraftRepository(store)

    repository.save(_make_document(workflow_id="wf-1", output_id="out-1"))

    key = store.write.call_args[0][0]
    assert key == "wf-1/out-1"
    assert key != "wf-1"


def test_save_translates_concurrent_modification_error():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = DraftRepository(store)

    with pytest.raises(DraftConcurrentModificationError):
        repository.save(_make_document(), expected_etag='"stale"')


def test_save_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = DraftRepository(store)

    with pytest.raises(DraftSerializationError):
        repository.save(_make_document())


def test_save_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("s3 down")
    repository = DraftRepository(store)

    with pytest.raises(DraftPersistenceError):
        repository.save(_make_document())
