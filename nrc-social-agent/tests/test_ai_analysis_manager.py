from unittest.mock import MagicMock

import pytest

from src.ai.analysis_manager import AnalysisManager
from src.ai.analysis_repository import LoadedAnalysis
from src.ai.errors import AnalysisConcurrentModificationError, AnalysisNotFoundError
from src.ai.models import AnalysisDocument, AnalysisStatus


def _make_document(**overrides):
    defaults = dict(
        analysis_id="an-1",
        workflow_id="wf-1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=AnalysisStatus.FAILED,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        media_type="photo",
    )
    defaults.update(overrides)
    return AnalysisDocument(**defaults)


# --- find_existing -----------------------------------------------------


def test_find_existing_returns_none_when_not_found():
    repository = MagicMock()
    repository.load.side_effect = AnalysisNotFoundError("none")
    manager = AnalysisManager(repository)

    assert manager.find_existing("wf-1") is None


def test_find_existing_returns_document_when_found():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedAnalysis(document=document, etag='"etag-1"')
    manager = AnalysisManager(repository)

    assert manager.find_existing("wf-1") == document


# --- create_analysis -----------------------------------------------------


def test_create_analysis_persists_with_conditional_create():
    repository = MagicMock()
    manager = AnalysisManager(repository)

    document = manager.create_analysis(
        workflow_id="wf-1",
        model="claude-opus-5",
        schema_version=1,
        prompt_version=1,
        media_type="photo",
        status=AnalysisStatus.COMPLETED,
    )

    assert document.workflow_id == "wf-1"
    assert document.status is AnalysisStatus.COMPLETED
    assert document.created_at == document.updated_at

    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert saved_document[0] is document
    assert kwargs["expected_etag"] is None  # never silently overwrite an existing analysis


def test_create_analysis_generates_a_fresh_analysis_id_each_call():
    repository = MagicMock()
    manager = AnalysisManager(repository)

    first = manager.create_analysis(
        workflow_id="wf-1", model="m", schema_version=1, prompt_version=1,
        media_type="photo", status=AnalysisStatus.COMPLETED,
    )
    second = manager.create_analysis(
        workflow_id="wf-2", model="m", schema_version=1, prompt_version=1,
        media_type="photo", status=AnalysisStatus.COMPLETED,
    )

    assert first.analysis_id != second.analysis_id


def test_create_analysis_propagates_concurrent_modification_when_one_already_exists():
    repository = MagicMock()
    repository.save.side_effect = AnalysisConcurrentModificationError("already exists")
    manager = AnalysisManager(repository)

    with pytest.raises(AnalysisConcurrentModificationError):
        manager.create_analysis(
            workflow_id="wf-1", model="m", schema_version=1, prompt_version=1,
            media_type="photo", status=AnalysisStatus.COMPLETED,
        )


# --- supersede_failed_analysis -------------------------------------------


def test_supersede_failed_analysis_saves_with_etag_from_load():
    existing = _make_document(status=AnalysisStatus.FAILED)
    repository = MagicMock()
    repository.load.return_value = LoadedAnalysis(document=existing, etag='"etag-1"')
    manager = AnalysisManager(repository)

    updated = manager.supersede_failed_analysis(
        workflow_id="wf-1", model="claude-opus-5", schema_version=1, prompt_version=1,
        media_type="photo", status=AnalysisStatus.COMPLETED,
    )

    assert updated.status is AnalysisStatus.COMPLETED
    assert updated.analysis_id == existing.analysis_id  # same record, not a new one
    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_supersede_failed_analysis_preserves_created_at():
    existing = _make_document(created_at="2020-01-01T00:00:00+00:00")
    repository = MagicMock()
    repository.load.return_value = LoadedAnalysis(document=existing, etag='"etag-1"')
    manager = AnalysisManager(repository)

    updated = manager.supersede_failed_analysis(
        workflow_id="wf-1", model="m", schema_version=1, prompt_version=1,
        media_type="photo", status=AnalysisStatus.COMPLETED,
    )

    assert updated.created_at == "2020-01-01T00:00:00+00:00"
    assert updated.updated_at != existing.updated_at


def test_supersede_failed_analysis_propagates_concurrent_modification_error():
    existing = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedAnalysis(document=existing, etag='"stale"')
    repository.save.side_effect = AnalysisConcurrentModificationError("changed since load")
    manager = AnalysisManager(repository)

    with pytest.raises(AnalysisConcurrentModificationError):
        manager.supersede_failed_analysis(
            workflow_id="wf-1", model="m", schema_version=1, prompt_version=1,
            media_type="photo", status=AnalysisStatus.COMPLETED,
        )
