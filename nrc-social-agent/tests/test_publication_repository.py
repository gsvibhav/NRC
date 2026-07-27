from unittest.mock import MagicMock

import pytest

from src.publication.errors import (
    PublicationConcurrentModificationError,
    PublicationDeserializationError,
    PublicationNotFoundError,
    PublicationPersistenceError,
    PublicationSerializationError,
)
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publication.repository import LoadedPublication, PublicationRepository
from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_document(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1,
        created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


def test_load_returns_document_and_etag_and_uses_composite_key():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = PublicationRepository(store)

    result = repository.load("wf-1", "out-1")

    assert isinstance(result, LoadedPublication)
    assert result.document == document
    assert result.etag == '"etag-1"'
    store.read.assert_called_once_with("wf-1/out-1")


def test_load_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationNotFoundError):
        repository.load("wf-1", "out-1")


def test_load_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationDeserializationError):
        repository.load("wf-1", "out-1")


def test_load_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("boom")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationPersistenceError):
        repository.load("wf-1", "out-1")


def test_save_conditional_create_uses_none_etag():
    store = MagicMock()
    store.write.return_value = '"etag-2"'
    repository = PublicationRepository(store)
    document = _make_document()

    etag = repository.save(document, expected_etag=None)

    assert etag == '"etag-2"'
    store.write.assert_called_once_with("wf-1/out-1", document.to_dict(), expected_etag=None)


def test_save_translates_concurrent_modification_error():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationConcurrentModificationError):
        repository.save(_make_document(), expected_etag='"stale"')


def test_save_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationSerializationError):
        repository.save(_make_document())


def test_save_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("boom")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationPersistenceError):
        repository.save(_make_document())


def test_exists_delegates_to_store_with_composite_key():
    store = MagicMock()
    store.exists.return_value = True
    repository = PublicationRepository(store)

    assert repository.exists("wf-1", "out-1") is True
    store.exists.assert_called_once_with("wf-1/out-1")


def test_exists_translates_generic_store_error():
    store = MagicMock()
    store.exists.side_effect = ObjectStoreError("boom")
    repository = PublicationRepository(store)

    with pytest.raises(PublicationPersistenceError):
        repository.exists("wf-1", "out-1")
