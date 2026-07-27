import pytest

from src.execution.errors import ExecutionValidationFailedError
from src.execution.models import ExecutionDocument, ExecutionStatus
from src.execution.validation import validate_execution
from src.publication.models import PublicationChannel


def _make_document(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def test_valid_execution_passes():
    validate_execution(_make_document())


def test_rejects_non_ready_for_dispatch_status():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(status=ExecutionStatus.DISPATCH_IN_PROGRESS))


def test_rejects_nonzero_attempt_on_a_freshly_created_execution():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(attempt=1))


def test_rejects_missing_publisher():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(publisher=""))


def test_rejects_banned_metadata_key_caption():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(), metadata={"caption": "..."})


def test_rejects_banned_metadata_key_platform_response():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(), metadata={"platform_response": "..."})


def test_rejects_banned_metadata_key_from_documents_own_field():
    with pytest.raises(ExecutionValidationFailedError):
        validate_execution(_make_document(metadata={"instagram_post_id": "123"}))


def test_allows_benign_metadata_key():
    validate_execution(_make_document(), metadata={"note": "internal only"})
