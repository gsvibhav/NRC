from unittest.mock import MagicMock

import pytest

from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_manager import DraftVersionManager, resolve_current_draft
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.ai.draft_version_repository import LoadedCurrentPointer, LoadedDraftVersion
from src.ai.errors import (
    CurrentDraftPointerNotFoundError,
    DraftVersionConflictError,
    MissingCurrentDraftForEditError,
)


def _make_legacy_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=1, prompt_version=1, model="claude-opus-5",
        content={"caption": "A great caption.", "hashtags": [], "cta": None},
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


# --- get_current -----------------------------------------------------------


def test_get_current_returns_none_when_no_pointer_exists():
    repository = MagicMock()
    repository.load_pointer.side_effect = CurrentDraftPointerNotFoundError("none")
    manager = DraftVersionManager(repository)

    assert manager.get_current("wf-1", "out-1") is None


def test_get_current_returns_document_pointer_and_etag_when_found():
    repository = MagicMock()
    pointer = _make_pointer()
    document = _make_legacy_document()
    repository.load_pointer.return_value = LoadedCurrentPointer(pointer=pointer, etag='"p-etag"')
    repository.load_version.return_value = LoadedDraftVersion(document=document, etag='"v-etag"')
    manager = DraftVersionManager(repository)

    result = manager.get_current("wf-1", "out-1")

    assert result == (document, pointer, '"p-etag"')
    repository.load_version.assert_called_once_with("wf-1", "out-1", 1)


# --- ensure_migrated ---------------------------------------------------


def test_ensure_migrated_creates_version_1_and_pointer_when_none_exists():
    repository = MagicMock()
    repository.load_pointer.side_effect = [
        CurrentDraftPointerNotFoundError("none"),  # initial get_current() check
        LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"'),  # final re-read
    ]
    repository.load_version.return_value = LoadedDraftVersion(document=_make_legacy_document(), etag='"v-etag"')
    manager = DraftVersionManager(repository)
    legacy = _make_legacy_document()

    document, pointer, etag = manager.ensure_migrated("wf-1", "out-1", legacy)

    repository.save_version.assert_called_once()
    saved_version = repository.save_version.call_args[0][0]
    assert saved_version.version_number == 1
    assert saved_version.parent_version_number is None
    assert saved_version.draft_id == legacy.draft_id

    repository.save_pointer.assert_called_once()
    saved_pointer, kwargs = repository.save_pointer.call_args
    assert saved_pointer[0].current_version_number == 1
    assert kwargs["expected_etag"] is None

    assert document == _make_legacy_document()
    assert pointer.current_version_number == 1


def test_ensure_migrated_is_a_no_op_if_already_migrated():
    repository = MagicMock()
    pointer = _make_pointer()
    document = _make_legacy_document()
    repository.load_pointer.return_value = LoadedCurrentPointer(pointer=pointer, etag='"p-etag"')
    repository.load_version.return_value = LoadedDraftVersion(document=document, etag='"v-etag"')
    manager = DraftVersionManager(repository)

    result = manager.ensure_migrated("wf-1", "out-1", _make_legacy_document())

    assert result == (document, pointer, '"p-etag"')
    repository.save_version.assert_not_called()
    repository.save_pointer.assert_not_called()


def test_ensure_migrated_handles_concurrent_migration_race_on_version():
    repository = MagicMock()
    repository.load_pointer.side_effect = [
        CurrentDraftPointerNotFoundError("none"),
        LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"'),
    ]
    repository.load_version.return_value = LoadedDraftVersion(document=_make_legacy_document(), etag='"v-etag"')
    repository.save_version.side_effect = DraftVersionConflictError("already exists")
    manager = DraftVersionManager(repository)

    # Must not raise -- a lost race on version creation is not an error,
    # just someone else winning the migration first.
    document, pointer, etag = manager.ensure_migrated("wf-1", "out-1", _make_legacy_document())

    assert pointer.current_version_number == 1


def test_ensure_migrated_handles_concurrent_migration_race_on_pointer():
    repository = MagicMock()
    repository.load_pointer.side_effect = [
        CurrentDraftPointerNotFoundError("none"),
        LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"'),
    ]
    repository.load_version.return_value = LoadedDraftVersion(document=_make_legacy_document(), etag='"v-etag"')
    repository.save_pointer.side_effect = DraftVersionConflictError("already exists")
    manager = DraftVersionManager(repository)

    document, pointer, etag = manager.ensure_migrated("wf-1", "out-1", _make_legacy_document())

    assert pointer.current_version_number == 1


def test_ensure_migrated_never_mutates_the_legacy_draft_object():
    repository = MagicMock()
    repository.load_pointer.side_effect = [
        CurrentDraftPointerNotFoundError("none"),
        LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"'),
    ]
    repository.load_version.return_value = LoadedDraftVersion(document=_make_legacy_document(), etag='"v-etag"')
    manager = DraftVersionManager(repository)
    legacy = _make_legacy_document()

    manager.ensure_migrated("wf-1", "out-1", legacy)

    # The dataclass is frozen -- if it were ever mutated in place this
    # would already be impossible, but assert identity/equality survives
    # untouched as an explicit regression guard.
    assert legacy == _make_legacy_document()


# --- create_next_version -------------------------------------------------


def test_create_next_version_creates_version_and_advances_pointer():
    repository = MagicMock()
    manager = DraftVersionManager(repository)

    new_version = manager.create_next_version(
        workflow_id="wf-1", output_id="out-1", plan_id="plan-1", output_type="instagram_reel_caption",
        parent_version_number=1, expected_pointer_etag='"p-etag-1"',
        content={"caption": "Revised.", "hashtags": [], "cta": None}, model="claude-opus-5",
        schema_version=1, prompt_version=1, source_versions=None, usage=None,
        edit_instruction_reference={"instruction_id": "op-1", "created_at": "x"},
    )

    assert new_version.version_number == 2
    assert new_version.parent_version_number == 1
    assert new_version.edit_instruction_reference == {"instruction_id": "op-1", "created_at": "x"}

    repository.save_version.assert_called_once()
    saved_version = repository.save_version.call_args[0][0]
    assert saved_version.version_number == 2

    repository.save_pointer.assert_called_once()
    saved_pointer, kwargs = repository.save_pointer.call_args
    assert saved_pointer[0].current_version_number == 2
    assert saved_pointer[0].current_draft_id == new_version.draft_id
    assert kwargs["expected_etag"] == '"p-etag-1"'


def test_create_next_version_never_overwrites_earlier_versions():
    repository = MagicMock()
    manager = DraftVersionManager(repository)

    manager.create_next_version(
        workflow_id="wf-1", output_id="out-1", plan_id="plan-1", output_type="instagram_reel_caption",
        parent_version_number=2, expected_pointer_etag='"p-etag-2"',
        content={"caption": "Revised again.", "hashtags": [], "cta": None}, model="claude-opus-5",
        schema_version=1, prompt_version=1, source_versions=None, usage=None,
        edit_instruction_reference={"instruction_id": "op-2", "created_at": "x"},
    )

    saved_version = repository.save_version.call_args[0][0]
    assert saved_version.version_number == 3
    assert saved_version.parent_version_number == 2
    # save_version() is always a conditional create (see DraftVersionRepository) -- never an update.


def test_create_next_version_propagates_conflict_when_version_already_exists():
    repository = MagicMock()
    repository.save_version.side_effect = DraftVersionConflictError("already exists")
    manager = DraftVersionManager(repository)

    with pytest.raises(DraftVersionConflictError):
        manager.create_next_version(
            workflow_id="wf-1", output_id="out-1", plan_id="plan-1", output_type="instagram_reel_caption",
            parent_version_number=1, expected_pointer_etag='"p-etag-1"',
            content={"caption": "x", "hashtags": [], "cta": None}, model="m", schema_version=1, prompt_version=1,
            source_versions=None, usage=None, edit_instruction_reference={"instruction_id": "op-1", "created_at": "x"},
        )

    repository.save_pointer.assert_not_called()


def test_create_next_version_orphans_the_version_when_pointer_advance_loses_a_race():
    # The version write succeeds, but the pointer write's OCC check fails
    # (someone else advanced it in between) -- the version record is left
    # in place (never deleted/renumbered), and the conflict propagates so
    # the caller can respond safely (see draft_editing_service.py).
    repository = MagicMock()
    repository.save_pointer.side_effect = DraftVersionConflictError("pointer moved")
    manager = DraftVersionManager(repository)

    with pytest.raises(DraftVersionConflictError):
        manager.create_next_version(
            workflow_id="wf-1", output_id="out-1", plan_id="plan-1", output_type="instagram_reel_caption",
            parent_version_number=1, expected_pointer_etag='"stale-etag"',
            content={"caption": "x", "hashtags": [], "cta": None}, model="m", schema_version=1, prompt_version=1,
            source_versions=None, usage=None, edit_instruction_reference={"instruction_id": "op-1", "created_at": "x"},
        )

    # The version was still written (and is now an orphan) -- this call
    # already happened and is not retried or rolled back.
    repository.save_version.assert_called_once()


# --- update_pointer_status -------------------------------------------------


def test_update_pointer_status_approves_and_records_metadata():
    repository = MagicMock()
    repository.load_pointer.return_value = LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"')
    manager = DraftVersionManager(repository)

    updated = manager.update_pointer_status(
        workflow_id="wf-1", output_id="out-1", status=ReviewStatus.APPROVED,
        approved_at="2026-01-02T00:00:00+00:00", approved_by_telegram_user_id=42,
    )

    assert updated.status is ReviewStatus.APPROVED
    assert updated.approved_at == "2026-01-02T00:00:00+00:00"
    assert updated.approved_by_telegram_user_id == 42
    repository.save_pointer.assert_called_once()
    _, kwargs = repository.save_pointer.call_args
    assert kwargs["expected_etag"] == '"p-etag"'


def test_update_pointer_status_never_touches_the_immutable_version():
    repository = MagicMock()
    repository.load_pointer.return_value = LoadedCurrentPointer(pointer=_make_pointer(), etag='"p-etag"')
    manager = DraftVersionManager(repository)

    manager.update_pointer_status(workflow_id="wf-1", output_id="out-1", status=ReviewStatus.REJECTED)

    repository.save_version.assert_not_called()
    repository.load_version.assert_not_called()


def test_update_pointer_status_propagates_concurrent_modification_error():
    repository = MagicMock()
    repository.load_pointer.return_value = LoadedCurrentPointer(pointer=_make_pointer(), etag='"stale"')
    repository.save_pointer.side_effect = DraftVersionConflictError("changed since load")
    manager = DraftVersionManager(repository)

    with pytest.raises(DraftVersionConflictError):
        manager.update_pointer_status(workflow_id="wf-1", output_id="out-1", status=ReviewStatus.SAVED_AS_DRAFT)


# --- resolve_current_draft (shared migration entry point) ----------------


def test_resolve_current_draft_returns_existing_current_without_migrating():
    draft_version_manager = MagicMock()
    document = _make_legacy_document()
    pointer = _make_pointer()
    draft_version_manager.get_current.return_value = (document, pointer, '"etag-1"')
    draft_manager = MagicMock()

    result = resolve_current_draft(
        draft_version_manager=draft_version_manager, draft_manager=draft_manager,
        workflow_id="wf-1", output_id="out-1",
    )

    assert result == (document, pointer, '"etag-1"')
    draft_manager.find_existing.assert_not_called()
    draft_version_manager.ensure_migrated.assert_not_called()


def test_resolve_current_draft_migrates_legacy_draft_when_no_current_pointer():
    draft_version_manager = MagicMock()
    draft_version_manager.get_current.return_value = None
    document = _make_legacy_document()
    pointer = _make_pointer()
    draft_version_manager.ensure_migrated.return_value = (document, pointer, '"etag-1"')
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = document

    result = resolve_current_draft(
        draft_version_manager=draft_version_manager, draft_manager=draft_manager,
        workflow_id="wf-1", output_id="out-1",
    )

    assert result == (document, pointer, '"etag-1"')
    draft_manager.find_existing.assert_called_once_with("wf-1", "out-1")
    draft_version_manager.ensure_migrated.assert_called_once_with("wf-1", "out-1", document)


def test_resolve_current_draft_raises_when_neither_versioned_nor_legacy_draft_exists():
    draft_version_manager = MagicMock()
    draft_version_manager.get_current.return_value = None
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None

    with pytest.raises(MissingCurrentDraftForEditError):
        resolve_current_draft(
            draft_version_manager=draft_version_manager, draft_manager=draft_manager,
            workflow_id="wf-1", output_id="out-1",
        )
