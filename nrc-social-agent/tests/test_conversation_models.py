import pytest

from src.conversation.errors import (
    ConversationDeserializationError,
    ConversationVersionMismatchError,
    InvalidConversationStateError,
)
from src.conversation.models import CURRENT_VERSION, ConversationDocument
from src.conversation.states import ConversationState


def _make_document(**overrides):
    defaults = dict(
        telegram_user_id=42,
        active_workflow_id="wf-1",
        state=ConversationState.ACTIVE,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return ConversationDocument(**defaults)


def test_to_dict_includes_current_version_by_default():
    document = _make_document()

    data = document.to_dict()

    assert data["version"] == CURRENT_VERSION
    assert data["state"] == "ACTIVE"
    assert data["active_workflow_id"] == "wf-1"


def test_to_dict_has_exactly_the_documented_fields():
    document = _make_document()

    data = document.to_dict()

    assert set(data.keys()) == {
        "version",
        "telegram_user_id",
        "active_workflow_id",
        "state",
        "updated_at",
    }


def test_round_trip_to_dict_and_from_dict_preserves_all_fields():
    document = _make_document(state=ConversationState.IDLE, active_workflow_id=None)

    restored = ConversationDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_rejects_non_dict_input():
    with pytest.raises(ConversationDeserializationError):
        ConversationDocument.from_dict("not a dict")


def test_from_dict_rejects_missing_version():
    data = _make_document().to_dict()
    del data["version"]

    with pytest.raises(ConversationVersionMismatchError):
        ConversationDocument.from_dict(data)


def test_from_dict_rejects_unsupported_version():
    data = _make_document().to_dict()
    data["version"] = 999

    with pytest.raises(ConversationVersionMismatchError):
        ConversationDocument.from_dict(data)


def test_from_dict_rejects_missing_telegram_user_id():
    data = _make_document().to_dict()
    del data["telegram_user_id"]

    with pytest.raises(ConversationDeserializationError):
        ConversationDocument.from_dict(data)


def test_from_dict_rejects_non_integer_telegram_user_id():
    data = _make_document().to_dict()
    data["telegram_user_id"] = "42"

    with pytest.raises(ConversationDeserializationError):
        ConversationDocument.from_dict(data)


def test_from_dict_rejects_missing_updated_at():
    data = _make_document().to_dict()
    del data["updated_at"]

    with pytest.raises(ConversationDeserializationError):
        ConversationDocument.from_dict(data)


def test_from_dict_rejects_non_string_active_workflow_id():
    data = _make_document().to_dict()
    data["active_workflow_id"] = 12345

    with pytest.raises(ConversationDeserializationError):
        ConversationDocument.from_dict(data)


def test_from_dict_accepts_null_active_workflow_id():
    data = _make_document(active_workflow_id=None, state=ConversationState.IDLE).to_dict()

    document = ConversationDocument.from_dict(data)

    assert document.active_workflow_id is None


def test_from_dict_rejects_unrecognized_state():
    data = _make_document().to_dict()
    data["state"] = "NOT_A_REAL_STATE"

    with pytest.raises(InvalidConversationStateError):
        ConversationDocument.from_dict(data)
