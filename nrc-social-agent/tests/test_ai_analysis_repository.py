from unittest.mock import MagicMock

import pytest

from src.ai.analysis_repository import AnalysisRepository, LoadedAnalysis
from src.ai.errors import (
    AnalysisConcurrentModificationError,
    AnalysisDeserializationError,
    AnalysisNotFoundError,
    AnalysisPersistenceError,
    AnalysisSerializationError,
)
from src.ai.models import AnalysisDocument, AnalysisStatus
from src.storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_document(**overrides):
    defaults = dict(
        analysis_id="an-1",
        workflow_id="wf-1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=AnalysisStatus.COMPLETED,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        media_type="photo",
    )
    defaults.update(overrides)
    return AnalysisDocument(**defaults)


# --- load ------------------------------------------------------------------


def test_load_returns_document_and_etag_on_success():
    store = MagicMock()
    document = _make_document()
    store.read.return_value = (document.to_dict(), '"etag-1"')
    repository = AnalysisRepository(store)

    result = repository.load("wf-1")

    assert isinstance(result, LoadedAnalysis)
    assert result.document == document
    assert result.etag == '"etag-1"'


def test_load_translates_not_found_error():
    store = MagicMock()
    store.read.side_effect = ObjectNotFoundError("no object")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisNotFoundError):
        repository.load("wf-1")


def test_load_translates_deserialization_error():
    store = MagicMock()
    store.read.side_effect = ObjectDeserializationError("bad json")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisDeserializationError):
        repository.load("wf-1")


def test_load_translates_generic_store_error():
    store = MagicMock()
    store.read.side_effect = ObjectStoreError("s3 down")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisPersistenceError):
        repository.load("wf-1")


# --- save --------------------------------------------------------------


def test_save_writes_document_dict_and_returns_etag():
    store = MagicMock()
    store.write.return_value = '"new-etag"'
    repository = AnalysisRepository(store)
    document = _make_document()

    etag = repository.save(document, expected_etag=None)

    assert etag == '"new-etag"'
    store.write.assert_called_once_with("wf-1", document.to_dict(), expected_etag=None)


def test_save_translates_concurrent_modification_error():
    store = MagicMock()
    store.write.side_effect = ObjectConcurrentModificationError("conflict")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisConcurrentModificationError):
        repository.save(_make_document(), expected_etag='"stale"')


def test_save_translates_serialization_error():
    store = MagicMock()
    store.write.side_effect = ObjectSerializationError("bad data")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisSerializationError):
        repository.save(_make_document())


def test_save_translates_generic_store_error():
    store = MagicMock()
    store.write.side_effect = ObjectStoreError("s3 down")
    repository = AnalysisRepository(store)

    with pytest.raises(AnalysisPersistenceError):
        repository.save(_make_document())
