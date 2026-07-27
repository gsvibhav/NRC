from unittest.mock import MagicMock

import pytest

from src.ai.content_plan_manager import ContentPlanManager
from src.ai.content_plan_repository import LoadedContentPlan
from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus, OutputType, PlannedOutput
from src.ai.errors import (
    ContentPlanConcurrentModificationError,
    ContentPlanNotFoundError,
    ContentPlanSchemaMismatchError,
)


def _make_output(**overrides):
    defaults = dict(
        output_id="out-1",
        output_type=OutputType.INSTAGRAM_REEL_CAPTION.value,
        priority=1,
        purpose="build founder credibility",
        message_focus="make the idea immediate and memorable",
        cta_direction="invite viewers to explore NRC without a hard sell",
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


def _make_document(**overrides):
    defaults = dict(
        plan_id="plan-1", workflow_id="wf-1", created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00", status=ContentPlanStatus.FAILED,
        schema_version=1, prompt_version=1, model="claude-opus-5",
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


def test_find_existing_returns_none_when_not_found():
    repository = MagicMock()
    repository.load.side_effect = ContentPlanNotFoundError("none")
    manager = ContentPlanManager(repository)

    assert manager.find_existing("wf-1") is None


def test_find_existing_returns_document_when_found():
    document = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=document, etag='"etag-1"')
    manager = ContentPlanManager(repository)

    assert manager.find_existing("wf-1") == document


def test_create_content_plan_persists_with_conditional_create():
    repository = MagicMock()
    manager = ContentPlanManager(repository)

    document = manager.create_content_plan(
        workflow_id="wf-1", model="claude-opus-5", schema_version=1, prompt_version=1,
        status=ContentPlanStatus.COMPLETED,
    )

    assert document.workflow_id == "wf-1"
    assert document.status is ContentPlanStatus.COMPLETED
    assert document.created_at == document.updated_at
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] is None


def test_create_content_plan_generates_a_fresh_plan_id_each_call():
    repository = MagicMock()
    manager = ContentPlanManager(repository)

    first = manager.create_content_plan(
        workflow_id="wf-1", model="m", schema_version=1, prompt_version=1, status=ContentPlanStatus.COMPLETED,
    )
    second = manager.create_content_plan(
        workflow_id="wf-2", model="m", schema_version=1, prompt_version=1, status=ContentPlanStatus.COMPLETED,
    )

    assert first.plan_id != second.plan_id


def test_create_content_plan_propagates_concurrent_modification_when_one_already_exists():
    repository = MagicMock()
    repository.save.side_effect = ContentPlanConcurrentModificationError("already exists")
    manager = ContentPlanManager(repository)

    with pytest.raises(ContentPlanConcurrentModificationError):
        manager.create_content_plan(
            workflow_id="wf-1", model="m", schema_version=1, prompt_version=1, status=ContentPlanStatus.COMPLETED,
        )


def test_supersede_failed_content_plan_saves_with_etag_from_load():
    existing = _make_document(status=ContentPlanStatus.FAILED)
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"etag-1"')
    manager = ContentPlanManager(repository)

    updated = manager.supersede_failed_content_plan(
        workflow_id="wf-1", model="claude-opus-5", schema_version=1, prompt_version=1,
        status=ContentPlanStatus.COMPLETED,
    )

    assert updated.status is ContentPlanStatus.COMPLETED
    assert updated.plan_id == existing.plan_id
    repository.save.assert_called_once()
    _, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_supersede_failed_content_plan_preserves_created_at():
    existing = _make_document(created_at="2020-01-01T00:00:00+00:00")
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"etag-1"')
    manager = ContentPlanManager(repository)

    updated = manager.supersede_failed_content_plan(
        workflow_id="wf-1", model="m", schema_version=1, prompt_version=1, status=ContentPlanStatus.COMPLETED,
    )

    assert updated.created_at == "2020-01-01T00:00:00+00:00"
    assert updated.updated_at != existing.updated_at


def test_supersede_failed_content_plan_propagates_concurrent_modification_error():
    existing = _make_document()
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"stale"')
    repository.save.side_effect = ContentPlanConcurrentModificationError("changed since load")
    manager = ContentPlanManager(repository)

    with pytest.raises(ContentPlanConcurrentModificationError):
        manager.supersede_failed_content_plan(
            workflow_id="wf-1", model="m", schema_version=1, prompt_version=1, status=ContentPlanStatus.COMPLETED,
        )


# --- mark_output_generated (Milestone 6) ---------------------------------


def test_mark_output_generated_flips_generation_status_for_matching_output():
    existing = _make_document(
        status=ContentPlanStatus.COMPLETED,
        outputs=[_make_output(output_id="out-1"), _make_output(output_id="out-2", priority=2)],
    )
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"etag-1"')
    manager = ContentPlanManager(repository)

    updated = manager.mark_output_generated(workflow_id="wf-1", output_id="out-1")

    by_id = {o.output_id: o for o in updated.outputs}
    assert by_id["out-1"].generation_status == "GENERATED"
    assert by_id["out-2"].generation_status == "NOT_STARTED"
    repository.save.assert_called_once()
    saved_document, kwargs = repository.save.call_args
    assert kwargs["expected_etag"] == '"etag-1"'


def test_mark_output_generated_raises_when_output_id_not_found():
    existing = _make_document(status=ContentPlanStatus.COMPLETED, outputs=[_make_output(output_id="out-1")])
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"etag-1"')
    manager = ContentPlanManager(repository)

    with pytest.raises(ContentPlanSchemaMismatchError):
        manager.mark_output_generated(workflow_id="wf-1", output_id="does-not-exist")

    repository.save.assert_not_called()


def test_mark_output_generated_propagates_concurrent_modification_error():
    existing = _make_document(status=ContentPlanStatus.COMPLETED, outputs=[_make_output(output_id="out-1")])
    repository = MagicMock()
    repository.load.return_value = LoadedContentPlan(document=existing, etag='"stale"')
    repository.save.side_effect = ContentPlanConcurrentModificationError("changed since load")
    manager = ContentPlanManager(repository)

    with pytest.raises(ContentPlanConcurrentModificationError):
        manager.mark_output_generated(workflow_id="wf-1", output_id="out-1")
