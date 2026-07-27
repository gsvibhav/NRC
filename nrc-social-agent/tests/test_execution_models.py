import pytest

from src.execution.errors import (
    ExecutionDeserializationError,
    ExecutionVersionMismatchError,
    InvalidExecutionStatusError,
    UnsupportedExecutionChannelError,
)
from src.execution.models import ExecutionDocument, ExecutionStatus, resolve_publisher
from src.publication.models import PublicationChannel


def _make_document(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


# --- resolve_publisher ---------------------------------------------------


def test_resolve_publisher_maps_instagram():
    assert resolve_publisher(PublicationChannel.INSTAGRAM) == "instagram"


def test_resolve_publisher_rejects_unknown_channel():
    class _FakeChannel:
        pass

    with pytest.raises(UnsupportedExecutionChannelError):
        resolve_publisher(_FakeChannel())


# --- ExecutionDocument -----------------------------------------------------


def test_execution_document_round_trips():
    document = _make_document()
    assert ExecutionDocument.from_dict(document.to_dict()) == document


def test_execution_document_from_dict_rejects_unsupported_schema_version():
    data = _make_document().to_dict()
    data["schema_version"] = 999
    with pytest.raises(ExecutionVersionMismatchError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_missing_required_field():
    data = _make_document().to_dict()
    del data["workflow_id"]
    with pytest.raises(ExecutionDeserializationError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_unrecognized_channel():
    data = _make_document().to_dict()
    data["channel"] = "tiktok"
    with pytest.raises(ExecutionDeserializationError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_unrecognized_status():
    data = _make_document().to_dict()
    data["status"] = "PUBLISHING"
    with pytest.raises(InvalidExecutionStatusError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_negative_attempt():
    data = _make_document().to_dict()
    data["attempt"] = -1
    with pytest.raises(ExecutionDeserializationError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_bool_as_attempt():
    data = _make_document().to_dict()
    data["attempt"] = True
    with pytest.raises(ExecutionDeserializationError):
        ExecutionDocument.from_dict(data)


def test_execution_document_from_dict_rejects_missing_publisher():
    data = _make_document().to_dict()
    data["publisher"] = ""
    with pytest.raises(ExecutionDeserializationError):
        ExecutionDocument.from_dict(data)


def test_execution_document_defaults_metadata_to_empty_dict():
    document = _make_document(metadata={})
    assert document.to_dict()["metadata"] == {}


def test_execution_document_never_has_a_field_for_platform_response_or_post_id():
    # Structural contract: these field names must never exist on the
    # dataclass, so a platform response/post id could never be attached
    # even by accident.
    field_names = set(_make_document().__dataclass_fields__)
    for forbidden in ("platform_response", "instagram_post_id", "caption", "hashtags", "prompt"):
        assert forbidden not in field_names


def test_only_ready_for_dispatch_and_three_reserved_future_statuses_exist():
    assert {s.value for s in ExecutionStatus} == {
        "READY_FOR_DISPATCH", "DISPATCH_IN_PROGRESS", "DISPATCH_FAILED", "COMPLETED",
    }
