from unittest.mock import MagicMock

from src.execution.manager import ExecutionManager
from src.execution.models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from src.execution.repository import LoadedExecution
from src.publication.models import PublicationChannel


def _make_document(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def test_load_returns_document_and_etag():
    repository = MagicMock()
    document = _make_document()
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)

    result = manager.load("pub-1")

    assert result == (document, '"etag-1"')


def test_authorize_dispatch_persists_authorization_without_changing_status():
    repository = MagicMock()
    document = _make_document()
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)
    authorization = {"authorized_at": "t0", "authorized_by_telegram_user_id": 1}

    updated, etag = manager.authorize_dispatch(publication_id="pub-1", expected_etag='"etag-1"', authorization=authorization)

    assert updated.dispatch_authorization == authorization
    assert updated.status is ExecutionStatus.READY_FOR_DISPATCH
    repository.save.assert_called_once_with(updated, expected_etag='"etag-1"')


def test_claim_dispatch_transitions_to_in_progress_and_sets_lease():
    repository = MagicMock()
    document = _make_document(checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state={"container_id": "c1"})
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)
    lease = {"owner_id": "o1", "acquired_at": "t1", "expires_at": "t2"}

    updated, etag = manager.claim_dispatch(publication_id="pub-1", expected_etag='"etag-1"', lease=lease, increment_attempt=True)

    assert updated.status is ExecutionStatus.DISPATCH_IN_PROGRESS
    assert updated.lease == lease
    assert updated.attempt == 1
    # checkpoint/platform_state preserved, never reset
    assert updated.checkpoint is DispatchCheckpoint.CONTAINER_CREATED
    assert updated.platform_state == {"container_id": "c1"}


def test_claim_dispatch_does_not_increment_attempt_when_recovering():
    repository = MagicMock()
    document = _make_document(attempt=1)
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)

    updated, etag = manager.claim_dispatch(
        publication_id="pub-1", expected_etag='"etag-1"', lease={"owner_id": "o1", "acquired_at": "t1", "expires_at": "t2"},
        increment_attempt=False,
    )

    assert updated.attempt == 1


def test_persist_checkpoint_merges_platform_state():
    repository = MagicMock()
    document = _make_document(platform_state={"container_id": "c1"})
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)

    updated, etag = manager.persist_checkpoint(
        publication_id="pub-1", expected_etag='"etag-1"', checkpoint=DispatchCheckpoint.CONTAINER_READY,
        platform_state_update={"last_status": "FINISHED"},
    )

    assert updated.checkpoint is DispatchCheckpoint.CONTAINER_READY
    assert updated.platform_state == {"container_id": "c1", "last_status": "FINISHED"}


def test_complete_dispatch_sets_completed_status_and_clears_lease_and_failure():
    repository = MagicMock()
    document = _make_document(
        status=ExecutionStatus.DISPATCH_IN_PROGRESS, lease={"owner_id": "o1", "acquired_at": "t", "expires_at": "t2"},
        failure={"category": "RATE_LIMITED", "retryable": True},
    )
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)
    result = {"platform": "instagram", "external_media_id": "m1"}

    updated = manager.complete_dispatch(
        publication_id="pub-1", expected_etag='"etag-1"', checkpoint=DispatchCheckpoint.VERIFIED, result=result,
    )

    assert updated.status is ExecutionStatus.COMPLETED
    assert updated.checkpoint is DispatchCheckpoint.VERIFIED
    assert updated.result == result
    assert updated.failure is None
    assert updated.lease is None


def test_fail_dispatch_preserves_checkpoint_by_default():
    repository = MagicMock()
    document = _make_document(
        status=ExecutionStatus.DISPATCH_IN_PROGRESS, checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING,
        lease={"owner_id": "o1", "acquired_at": "t", "expires_at": "t2"},
    )
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)
    failure = {"category": "RATE_LIMITED", "retryable": True}

    updated = manager.fail_dispatch(publication_id="pub-1", expected_etag='"etag-1"', failure=failure)

    assert updated.status is ExecutionStatus.DISPATCH_FAILED
    assert updated.checkpoint is DispatchCheckpoint.CONTAINER_PROCESSING
    assert updated.failure == failure
    assert updated.lease is None


def test_fail_dispatch_resets_checkpoint_when_explicitly_requested():
    repository = MagicMock()
    document = _make_document(status=ExecutionStatus.DISPATCH_IN_PROGRESS, checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING)
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)

    updated = manager.fail_dispatch(
        publication_id="pub-1", expected_etag='"etag-1"', failure={"category": "CONTAINER_REJECTED", "retryable": False},
        checkpoint=DispatchCheckpoint.NOT_STARTED,
    )

    assert updated.checkpoint is DispatchCheckpoint.NOT_STARTED
