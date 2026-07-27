from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src.execution.clock import Clock, Sleeper
from src.execution.dispatch_service import ExecutionDispatchService
from src.execution.errors import (
    MissingDispatchAuthorizationError,
    PublisherChannelMismatchError,
    UnauthorizedDispatchError,
)
from src.execution.models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publisher.errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from src.publisher.models import FailureCategory, PublisherFailure, PublisherResult, PublisherStepResult
from src.publisher.registry import PublisherRegistry
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


class _FakeClock(Clock):
    def __init__(self):
        self.n = 0

    def now_iso(self):
        self.n += 1
        return f"2026-01-01T00:00:{self.n:02d}+00:00"


class _FakeSleeper(Sleeper):
    def __init__(self):
        self.calls = 0

    async def sleep(self, seconds):
        self.calls += 1


class _StatefulExecutionManager:
    """A real, in-memory implementation of the ExecutionManager surface
    the dispatch service needs, so tests exercise the actual state
    machine (claim -> checkpoint advances -> complete/fail) rather than
    hand-wiring a MagicMock per test."""

    def __init__(self, document: ExecutionDocument):
        self.document = document
        self._etag_counter = 0
        self.fail_dispatch_calls = []

    def _next_etag(self):
        self._etag_counter += 1
        return f'"etag-{self._etag_counter}"'

    def load(self, publication_id):
        return self.document, self._next_etag()

    def find_existing(self, publication_id):
        return self.document

    def authorize_dispatch(self, *, publication_id, expected_etag, authorization):
        self.document = replace(self.document, dispatch_authorization=authorization)
        return self.document, self._next_etag()

    def claim_dispatch(self, *, publication_id, expected_etag, lease, increment_attempt):
        self.document = replace(
            self.document, status=ExecutionStatus.DISPATCH_IN_PROGRESS, lease=lease,
            attempt=self.document.attempt + (1 if increment_attempt else 0),
        )
        return self.document, self._next_etag()

    def persist_checkpoint(self, *, publication_id, expected_etag, checkpoint, platform_state_update):
        merged = {**(self.document.platform_state or {}), **(platform_state_update or {})}
        self.document = replace(self.document, checkpoint=checkpoint, platform_state=merged)
        return self.document, self._next_etag()

    def complete_dispatch(self, *, publication_id, expected_etag, checkpoint, result):
        self.document = replace(
            self.document, status=ExecutionStatus.COMPLETED, checkpoint=checkpoint, result=result, lease=None,
        )
        return self.document

    def fail_dispatch(self, *, publication_id, expected_etag, failure, checkpoint=None):
        self.fail_dispatch_calls.append(failure)
        self.document = replace(
            self.document, status=ExecutionStatus.DISPATCH_FAILED, failure=failure, lease=None,
            checkpoint=checkpoint or self.document.checkpoint,
        )
        return self.document


def _make_execution(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0, publisher="instagram", created_at="t", updated_at="t",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def _make_package(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1, created_at="t", updated_at="t",
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


def _make_workflow(**overrides) -> WorkflowDocument:
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=42, created_at="t", updated_at="t", state=WorkflowState.COMPLETED,
        media={}, publication={"publication_id": "pub-1", "output_id": "out-1", "status": "READY_FOR_PUBLISHING"},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_service(*, execution_manager, publisher, workflow_manager=None, publication_manager=None, conversation_manager=None):
    workflow_manager = workflow_manager or MagicMock()
    if workflow_manager.load_workflow.return_value is None or isinstance(workflow_manager.load_workflow.return_value, MagicMock):
        workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = publication_manager or MagicMock()
    if isinstance(publication_manager.find_existing.return_value, MagicMock):
        publication_manager.find_existing.return_value = _make_package()

    return ExecutionDispatchService(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager or MagicMock(),
        publisher_registry=PublisherRegistry({"instagram": publisher}), clock=_FakeClock(), sleeper=_FakeSleeper(),
        lease_seconds=120, poll_interval_seconds=1.0, poll_timeout_seconds=5.0,
    )


class _ScriptedPublisher:
    """A fake Publisher whose advance() returns a pre-scripted sequence of
    outcomes (results, exceptions, or PublisherStepResults), letting tests
    drive the dispatch loop deterministically without a real Instagram
    adapter."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.calls = []

    def advance(self, execution, publication):
        self.calls.append(execution.checkpoint)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _success_result():
    return PublisherResult(
        platform="instagram", external_media_id="m1", external_container_id="c1", permalink=None,
        published_at="t", verified_at="t", media_type="IMAGE",
    )


# --- happy path ------------------------------------------------------------


async def test_authorize_and_dispatch_completes_and_clears_pointer():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    conversation_manager = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher, conversation_manager=conversation_manager)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "completed"
    assert outcome.result["external_media_id"] == "m1"
    assert execution_manager.document.status is ExecutionStatus.COMPLETED
    assert execution_manager.document.dispatch_authorization is not None
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_dispatch_never_touches_the_publication_package():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    service = _make_service(execution_manager=execution_manager, publisher=publisher, publication_manager=publication_manager)

    await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert publication_manager.method_calls == [m for m in publication_manager.method_calls if m[0] == "find_existing"]


# --- ownership ---------------------------------------------------------


async def test_dispatch_rejects_an_unauthorized_user_with_zero_meta_calls():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = MagicMock()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(telegram_user_id=999)
    service = _make_service(execution_manager=execution_manager, publisher=publisher, workflow_manager=workflow_manager)

    with pytest.raises(UnauthorizedDispatchError):
        await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    publisher.advance.assert_not_called()


# --- idempotency ---------------------------------------------------------


async def test_dispatch_reuses_an_already_completed_execution_with_zero_meta_calls():
    execution_manager = _StatefulExecutionManager(
        _make_execution(status=ExecutionStatus.COMPLETED, result={"external_media_id": "m1"})
    )
    publisher = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "already_completed"
    assert outcome.result == {"external_media_id": "m1"}
    publisher.advance.assert_not_called()


async def test_dispatch_reports_in_progress_for_an_active_unexpired_lease():
    execution_manager = _StatefulExecutionManager(
        _make_execution(
            status=ExecutionStatus.DISPATCH_IN_PROGRESS,
            lease={"owner_id": "other", "acquired_at": "t", "expires_at": "9999-01-01T00:00:00+00:00"},
        )
    )
    publisher = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "in_progress"
    publisher.advance.assert_not_called()


async def test_dispatch_recovers_an_expired_lease_without_incrementing_attempt():
    execution_manager = _StatefulExecutionManager(
        _make_execution(
            status=ExecutionStatus.DISPATCH_IN_PROGRESS, attempt=1, checkpoint=DispatchCheckpoint.CONTAINER_READY,
            platform_state={"container_id": "c1"},
            lease={"owner_id": "other", "acquired_at": "t", "expires_at": "2020-01-01T00:00:00+00:00"},
            dispatch_authorization={"authorized_at": "t0", "authorized_by_telegram_user_id": 42},
        )
    )
    publisher = _ScriptedPublisher([PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result())])
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "completed"
    assert execution_manager.document.attempt == 1  # recovery, not a new attempt


async def test_dispatch_rejects_a_permanently_failed_execution_with_zero_meta_calls():
    execution_manager = _StatefulExecutionManager(
        _make_execution(status=ExecutionStatus.DISPATCH_FAILED, failure={"category": "INVALID_CREDENTIALS", "retryable": False})
    )
    publisher = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "permanent_failure"
    publisher.advance.assert_not_called()


# --- authorization ---------------------------------------------------------


async def test_retry_dispatch_requires_existing_authorization():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    with pytest.raises(MissingDispatchAuthorizationError):
        await service.retry_dispatch(publication_id="pub-1", telegram_user_id=42)

    publisher.advance.assert_not_called()


async def test_retry_dispatch_succeeds_once_authorization_already_exists():
    execution_manager = _StatefulExecutionManager(
        _make_execution(
            status=ExecutionStatus.DISPATCH_FAILED, failure={"category": "RATE_LIMITED", "retryable": True},
            dispatch_authorization={"authorized_at": "t0", "authorized_by_telegram_user_id": 42},
        )
    )
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.retry_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "completed"


# --- linkage -----------------------------------------------------------


async def test_dispatch_rejects_a_channel_mismatch():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package(publication_id="pub-DIFFERENT")
    publisher = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher, publication_manager=publication_manager)

    with pytest.raises(PublisherChannelMismatchError):
        await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    publisher.advance.assert_not_called()


# --- retryable failure ---------------------------------------------------


async def test_retryable_failure_sets_pending_retry_and_preserves_pointer():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = _ScriptedPublisher([PublisherRetryableError("rate limited", failure=PublisherFailure(
        category=FailureCategory.RATE_LIMITED, retryable=True, operation="create_media_container",
        safe_message="msg", occurred_at="t",
    ))])
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    conversation_manager = MagicMock()
    service = _make_service(
        execution_manager=execution_manager, publisher=publisher, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "retryable_failure"
    workflow_manager.update_metadata.assert_any_call("wf-1", {"pending_retry": "publication_dispatch"})
    conversation_manager.clear_active_workflow.assert_not_called()


async def test_permanent_failure_never_clears_pointer_and_preserves_package():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = _ScriptedPublisher([PublisherPermanentError("invalid creds")])
    conversation_manager = MagicMock()
    service = _make_service(execution_manager=execution_manager, publisher=publisher, conversation_manager=conversation_manager)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "permanent_failure"
    conversation_manager.clear_active_workflow.assert_not_called()


# --- ambiguous outcome reconciliation --------------------------------------


async def test_ambiguous_publish_is_retryable_and_does_not_increment_attempt_on_reconciliation():
    execution_manager = _StatefulExecutionManager(_make_execution())
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherAmbiguousError("ambiguous", partial_platform_state={"container_id": "c1"}),
    ])
    service = _make_service(execution_manager=execution_manager, publisher=publisher)

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "retryable_failure"
    assert execution_manager.document.checkpoint is DispatchCheckpoint.PUBLISH_REQUESTED
    assert execution_manager.document.attempt == 1

    # A later retry reconciles — never calls publish again, never
    # increments attempt further.
    publisher._steps.append(PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()))
    outcome2 = await service.retry_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome2.status == "completed"
    assert execution_manager.document.attempt == 1


# --- container-processing bounded polling ---------------------------------


async def test_container_processing_times_out_as_retryable_after_bounded_polls():
    execution_manager = _StatefulExecutionManager(_make_execution())
    steps = [PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"})]
    steps += [PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING) for _ in range(10)]
    publisher = _ScriptedPublisher(steps)
    sleeper = _FakeSleeper()
    service = _make_service(execution_manager=execution_manager, publisher=publisher)
    service._sleeper = sleeper  # poll_timeout_seconds=5 / interval=1 -> max 5 checks

    outcome = await service.authorize_and_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert outcome.status == "retryable_failure"
    assert execution_manager.document.failure["category"] == "CONTAINER_PROCESSING_TIMEOUT"
    assert sleeper.calls > 0
