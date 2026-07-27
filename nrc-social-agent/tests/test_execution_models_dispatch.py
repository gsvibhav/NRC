import pytest

from src.execution.errors import InvalidDispatchCheckpointError
from src.execution.models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from src.publication.models import PublicationChannel


def _make_document(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def test_v1_document_reads_back_with_not_started_checkpoint_and_no_dispatch_fields():
    # A Milestone-9 document, persisted before this milestone's schema
    # additions existed at all.
    v1_data = {
        "schema_version": 1, "execution_id": "exec-1", "publication_id": "pub-1", "workflow_id": "wf-1",
        "channel": "instagram", "status": "READY_FOR_DISPATCH", "attempt": 0, "publisher": "instagram",
        "created_at": "t1", "updated_at": "t1", "metadata": {},
    }

    document = ExecutionDocument.from_dict(v1_data)

    assert document.schema_version == 1
    assert document.checkpoint is DispatchCheckpoint.NOT_STARTED
    assert document.lease is None
    assert document.platform_state is None
    assert document.result is None
    assert document.failure is None
    assert document.dispatch_authorization is None


def test_v2_document_round_trips_with_dispatch_fields():
    document = _make_document(
        schema_version=2, status=ExecutionStatus.DISPATCH_IN_PROGRESS, checkpoint=DispatchCheckpoint.CONTAINER_CREATED,
        attempt=1, lease={"owner_id": "o1", "acquired_at": "t1", "expires_at": "t2"},
        platform_state={"container_id": "c1"}, dispatch_authorization={"authorized_at": "t0", "authorized_by_telegram_user_id": 1},
    )

    assert ExecutionDocument.from_dict(document.to_dict()) == document


def test_from_dict_rejects_unrecognized_checkpoint():
    data = _make_document().to_dict()
    data["checkpoint"] = "NOT_A_REAL_CHECKPOINT"
    with pytest.raises(InvalidDispatchCheckpointError):
        ExecutionDocument.from_dict(data)


def test_from_dict_rejects_non_dict_lease():
    data = _make_document().to_dict()
    data["lease"] = "not-a-dict"
    with pytest.raises(Exception):
        ExecutionDocument.from_dict(data)


def test_document_never_has_a_field_for_access_token_or_media_url():
    field_names = set(_make_document().__dataclass_fields__)
    for forbidden in ("access_token", "media_url", "presigned_url", "authorization_header", "raw_response"):
        assert forbidden not in field_names
