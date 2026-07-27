from unittest.mock import MagicMock

import pytest

from src.ai.draft_manager import DraftManager
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_repository import LoadedDraft
from src.ai.errors import DraftConcurrentModificationError, DraftNotFoundError


def _make_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00", status=DraftStatus.FAILED,
        schema_version=1, prompt_version=1, model="claude-opus-5",
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def test_find_existing_returns_none_when_not_found():
    repository = MagicMock()
    repository.load.side_effect = DraftNotFoundError("none")
    manager = DraftManager(repository)

    assert manager.find_existing("wf-1", "out-1") is None


def test_find_existing_returns_document_when_found():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedDraft(document=document, etag='"etag-1"')
    manager = DraftManager(repository)

    assert manager.find_existing("wf-1", "out-1") == document
    repository.load.assert_called_once_with("wf-1", "out-1")


def test_create_draft_persists_with_conditional_create():
    repository = MagicMock()
    manager = DraftManager(repository)

    document = manager.create_draft(
        workflow_id="wf-1", plan_id="plan-1", output_id="out-1", output_type="instagram_reel_caption",
        model="claude-opus-5", schema_version=1, prompt_version=1, status=DraftStatus.READY_FOR_REVIEW,
        content={"caption": "hi", "hashtags": [], "cta": None},
    )

    assert document.workflow_id == "wf-1"
    assert document.output_id == "out-1"
    assert document.status is DraftStatus.READY_FOR_REVIEW
    assert document.created_at == document.updated_at
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] is None


def test_create_draft_generates_a_fresh_draft_id_each_call():
    repository = MagicMock()
    manager = DraftManager(repository)

    first = manager.create_draft(
        workflow_id="wf-1", plan_id="plan-1", output_id="out-1", output_type="instagram_reel_caption",
        model="m", schema_version=1, prompt_version=1, status=DraftStatus.READY_FOR_REVIEW,
    )
    second = manager.create_draft(
        workflow_id="wf-2", plan_id="plan-2", output_id="out-2", output_type="instagram_reel_caption",
        model="m", schema_version=1, prompt_version=1, status=DraftStatus.READY_FOR_REVIEW,
    )

    assert first.draft_id != second.draft_id


def test_supersede_failed_draft_saves_with_etag_from_load():
    existing = _make_document(status=DraftStatus.FAILED)
    repository = MagicMock()
    repository.load.return_value = LoadedDraft(document=existing, etag='"etag-1"')
    manager = DraftManager(repository)

    updated = manager.supersede_failed_draft(
        workflow_id="wf-1", output_id="out-1", model="m", schema_version=1, prompt_version=1,
        status=DraftStatus.READY_FOR_REVIEW, content={"caption": "hi", "hashtags": [], "cta": None},
    )

    assert updated.status is DraftStatus.READY_FOR_REVIEW
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_supersede_failed_draft_preserves_created_at_and_identity_fields():
    existing = _make_document(created_at="2020-01-01T00:00:00+00:00", plan_id="plan-1", output_type="instagram_reel_caption")
    repository = MagicMock()
    repository.load.return_value = LoadedDraft(document=existing, etag='"etag-1"')
    manager = DraftManager(repository)

    updated = manager.supersede_failed_draft(
        workflow_id="wf-1", output_id="out-1", model="m", schema_version=1, prompt_version=1,
        status=DraftStatus.READY_FOR_REVIEW,
    )

    assert updated.created_at == "2020-01-01T00:00:00+00:00"
    assert updated.plan_id == "plan-1"
    assert updated.output_type == "instagram_reel_caption"
    assert updated.updated_at != existing.updated_at


def test_supersede_failed_draft_propagates_concurrent_modification_error():
    existing = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedDraft(document=existing, etag='"stale"')
    repository.save.side_effect = DraftConcurrentModificationError("changed since load")
    manager = DraftManager(repository)

    with pytest.raises(DraftConcurrentModificationError):
        manager.supersede_failed_draft(
            workflow_id="wf-1", output_id="out-1", model="m", schema_version=1, prompt_version=1,
            status=DraftStatus.READY_FOR_REVIEW,
        )
