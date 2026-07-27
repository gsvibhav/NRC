from unittest.mock import MagicMock

import pytest

from src.ai.content_plan_repository import ContentPlanRepository, LoadedContentPlan
from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus
from src.ai.errors import (
    ContentPlanConcurrentModificationError,
    ContentPlanDeserializationError,
    ContentPlanNotFoundError,
    ContentPlanPersistenceError,
    ContentPlanSerializationError,
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
        plan_id="plan-1", workflow_id="wf-1", created_at="x", updated_at="x",
        status=ContentPlanStatus.COMPLETED, schema_version=1, prompt_version=1, model="claude-opus-5",
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


def test_load_returns_document_and_etag_on_success():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = ContentPlanRepository(store)

    result = repository.load("wf-1")

    assert isinstance(result, LoadedContentPlan)
    assert result.document == document
    assert result.etag == '"etag-1"'


def test_load_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanNotFoundError):
        repository.load("wf-1")


def test_load_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanDeserializationError):
        repository.load("wf-1")


def test_load_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("s3 down")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanPersistenceError):
        repository.load("wf-1")


def test_save_writes_document_dict_and_returns_etag():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = ContentPlanRepository(store)
    document = _make_document()

    etag = repository.save(document, expected_etag=None)

    assert etag == '"new-etag"'
    store.write.assert_called_once_with("wf-1", document.to_dict(), expected_etag=None)


def test_save_translates_concurrent_modification_error():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanConcurrentModificationError):
        repository.save(_make_document(), expected_etag='"stale"')


def test_save_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanSerializationError):
        repository.save(_make_document())


def test_save_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("s3 down")
    repository = ContentPlanRepository(store)

    with pytest.raises(ContentPlanPersistenceError):
        repository.save(_make_document())
