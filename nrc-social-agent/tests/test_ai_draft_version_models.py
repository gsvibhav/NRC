import pytest

from src.ai.draft_version_models import (
    CURRENT_DOCUMENT_VERSION,
    SUPPORTED_DOCUMENT_VERSIONS,
    CurrentDraftPointer,
    ReviewStatus,
)
from src.ai.errors import (
    DraftVersionDeserializationError,
    DraftVersionDocumentVersionMismatchError,
)


def _make_pointer(**overrides):
    defaults = dict(
        workflow_id="wf-1", output_id="out-1", current_draft_id="draft-1", current_version_number=1,
        status=ReviewStatus.READY_FOR_REVIEW, updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return CurrentDraftPointer(**defaults)


def test_review_status_has_four_values():
    assert {s.value for s in ReviewStatus} == {"READY_FOR_REVIEW", "APPROVED", "SAVED_AS_DRAFT", "REJECTED"}


def test_current_draft_pointer_round_trips_through_dict():
    pointer = _make_pointer()

    assert CurrentDraftPointer.from_dict(pointer.to_dict()) == pointer


def test_current_draft_pointer_round_trips_approval_metadata():
    pointer = _make_pointer(
        status=ReviewStatus.APPROVED, approved_at="2026-01-02T00:00:00+00:00", approved_by_telegram_user_id=42,
    )

    restored = CurrentDraftPointer.from_dict(pointer.to_dict())

    assert restored.status is ReviewStatus.APPROVED
    assert restored.approved_at == "2026-01-02T00:00:00+00:00"
    assert restored.approved_by_telegram_user_id == 42


def test_current_draft_pointer_defaults_approval_fields_to_none():
    pointer = _make_pointer()

    assert pointer.approved_at is None
    assert pointer.approved_by_telegram_user_id is None


def test_current_draft_pointer_defaults_version_to_current():
    pointer = _make_pointer()

    assert pointer.version == CURRENT_DOCUMENT_VERSION


def test_current_draft_pointer_from_dict_rejects_non_dict():
    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict("not a dict")


def test_current_draft_pointer_from_dict_rejects_unsupported_version():
    data = _make_pointer().to_dict()
    data["version"] = 999

    with pytest.raises(DraftVersionDocumentVersionMismatchError):
        CurrentDraftPointer.from_dict(data)

    assert 999 not in SUPPORTED_DOCUMENT_VERSIONS


def test_current_draft_pointer_from_dict_rejects_missing_required_field():
    data = _make_pointer().to_dict()
    del data["current_draft_id"]

    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict(data)


def test_current_draft_pointer_from_dict_rejects_unrecognized_status():
    data = _make_pointer().to_dict()
    data["status"] = "SOMETHING_ELSE"

    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict(data)


def test_current_draft_pointer_from_dict_rejects_version_number_below_one():
    data = _make_pointer().to_dict()
    data["current_version_number"] = 0

    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict(data)


def test_current_draft_pointer_from_dict_rejects_non_int_approved_by():
    data = _make_pointer(status=ReviewStatus.APPROVED, approved_by_telegram_user_id=42).to_dict()
    data["approved_by_telegram_user_id"] = "42"

    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict(data)


def test_current_draft_pointer_from_dict_rejects_non_dict_metadata():
    data = _make_pointer().to_dict()
    data["metadata"] = "not a dict"

    with pytest.raises(DraftVersionDeserializationError):
        CurrentDraftPointer.from_dict(data)
