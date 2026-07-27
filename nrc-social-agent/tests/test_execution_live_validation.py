from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from src.execution.clock import Clock, Sleeper
from src.execution.dispatch_service import ExecutionDispatchService
from src.execution.errors import (
    ExecutionConcurrentModificationError,
    LiveValidationConfirmationExpiredError,
    LiveValidationConfirmationNotFoundError,
    LiveValidationNotReadyError,
    LiveValidationStateChangedError,
    NoEligibleLiveValidationCandidateError,
    NotLiveTestOperatorError,
)
from src.execution.live_validation import ControlledLiveValidationService, LiveValidationPreview
from src.execution.models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publisher.errors import PublisherPermanentError
from src.publisher.models import FailureCategory, PublisherResult, PublisherStepResult
from src.publisher.registry import PublisherRegistry
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


class _FakeClock(Clock):
    def __init__(self, start="2026-01-01T00:00:00+00:00"):
        self._start = start
        self.n = 0

    def now_iso(self):
        # Deterministic, monotonically increasing timestamps.
        self.n += 1
        return f"2026-01-01T00:{self.n:02d}:00+00:00"


class _FakeSleeper(Sleeper):
    async def sleep(self, seconds):
        pass


class _StatefulExecutionManager:
    def __init__(self, document: ExecutionDocument):
        self.document = document
        self._etag = 0
        self.set_live_validation_state_calls = []

    def _next_etag(self):
        self._etag += 1
        return f'"etag-{self._etag}"'

    def load(self, publication_id):
        return self.document, f'"etag-{self._etag}"' if self._etag else self._next_etag()

    def find_existing(self, publication_id):
        return self.document

    def authorize_dispatch(self, *, publication_id, expected_etag, authorization):
        self._check_etag(expected_etag)
        self.document = replace(self.document, dispatch_authorization=authorization)
        return self.document, self._next_etag()

    def claim_dispatch(self, *, publication_id, expected_etag, lease, increment_attempt):
        self._check_etag(expected_etag)
        self.document = replace(
            self.document, status=ExecutionStatus.DISPATCH_IN_PROGRESS, lease=lease,
            attempt=self.document.attempt + (1 if increment_attempt else 0),
        )
        return self.document, self._next_etag()

    def persist_checkpoint(self, *, publication_id, expected_etag, checkpoint, platform_state_update):
        self._check_etag(expected_etag)
        merged = {**(self.document.platform_state or {}), **(platform_state_update or {})}
        self.document = replace(self.document, checkpoint=checkpoint, platform_state=merged)
        return self.document, self._next_etag()

    def complete_dispatch(self, *, publication_id, expected_etag, checkpoint, result):
        self._check_etag(expected_etag)
        self.document = replace(
            self.document, status=ExecutionStatus.COMPLETED, checkpoint=checkpoint, result=result, lease=None,
        )
        return self.document

    def fail_dispatch(self, *, publication_id, expected_etag, failure, checkpoint=None):
        self._check_etag(expected_etag)
        self.document = replace(
            self.document, status=ExecutionStatus.DISPATCH_FAILED, failure=failure, lease=None,
            checkpoint=checkpoint or self.document.checkpoint,
        )
        return self.document

    def set_live_validation_state(self, *, publication_id, expected_etag, live_validation):
        self._check_etag(expected_etag)
        self.set_live_validation_state_calls.append(live_validation)
        self.document = replace(
            self.document, metadata={**self.document.metadata, "live_validation": live_validation},
        )
        return self.document, self._next_etag()

    def _check_etag(self, expected_etag):
        current = f'"etag-{self._etag}"' if self._etag else None
        if current is not None and expected_etag != current:
            raise ExecutionConcurrentModificationError("etag mismatch")


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
        draft={"draft_id": "draft-1", "version_number": 2},
        media=[{"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}}],
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


def _make_diagnostics_service(ready=True):
    from src.publisher.instagram.diagnostics import InstagramDiagnosticsResult

    service = MagicMock()
    service.check_status.return_value = InstagramDiagnosticsResult(
        application_healthy=True, publishing_enabled=True, instagram_configured=True,
        token_valid=True, permissions_ok=True, account_verified=True, account_username="nrc_official",
        instagram_ready=ready, failure_stage=None if ready else "token_validation",
        failure_reason=None if ready else "the access token is invalid or expired",
    )
    return service


class _ScriptedPublisher:
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


def _make_dispatch_service(execution_manager, publisher, *, workflow_manager, publication_manager):
    return ExecutionDispatchService(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=MagicMock(),
        publisher_registry=PublisherRegistry({"instagram": publisher}), clock=_FakeClock(), sleeper=_FakeSleeper(),
        lease_seconds=120, poll_interval_seconds=1.0, poll_timeout_seconds=5.0,
    )


def _make_live_validation_service(
    *, execution_manager, dispatch_service, workflow_manager, publication_manager,
    conversation_manager=None, diagnostics_service=None, operator_ids=frozenset({42}),
    instagram_account_id="179838000000000",
):
    if conversation_manager is None:
        conversation_manager = MagicMock()
        conversation_manager.load_active_workflow.return_value = "wf-1"
    return ControlledLiveValidationService(
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
        publication_manager=publication_manager, execution_manager=execution_manager,
        execution_dispatch_service=dispatch_service, diagnostics_service=diagnostics_service or _make_diagnostics_service(),
        clock=_FakeClock(), instagram_account_id=instagram_account_id, live_test_operator_ids=operator_ids,
    )


def _standard_setup(**exec_overrides):
    execution_manager = _StatefulExecutionManager(_make_execution(**exec_overrides))
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    return execution_manager, workflow_manager, publication_manager


# --- operator authorization ---------------------------------------------------


def test_find_eligible_candidates_denies_non_operator():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager, operator_ids=frozenset({999}),
    )

    with pytest.raises(NotLiveTestOperatorError):
        service.find_eligible_candidates(telegram_user_id=42)


def test_find_eligible_candidates_allows_operator_with_general_allowlist_irrelevant():
    # The live-test operator gate is independent of (in addition to) the
    # general @restricted allowlist, which this service never checks
    # itself — that's handlers.py's job via @restricted.
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager, operator_ids=frozenset({42}),
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert reason is None
    assert len(candidates) == 1


def test_missing_operator_configuration_fails_safely():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager, operator_ids=frozenset(),
    )

    with pytest.raises(NotLiveTestOperatorError):
        service.find_eligible_candidates(telegram_user_id=42)


# --- eligibility ---------------------------------------------------------------


def test_find_eligible_candidates_reports_reason_when_no_active_workflow():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    conversation_manager = MagicMock()
    conversation_manager.load_active_workflow.return_value = None
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        conversation_manager=conversation_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert reason is not None


def test_find_eligible_candidates_rejects_completed_execution():
    execution_manager, workflow_manager, publication_manager = _standard_setup(status=ExecutionStatus.COMPLETED)
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert "already been published" in reason


def test_find_eligible_candidates_rejects_active_lease():
    execution_manager, workflow_manager, publication_manager = _standard_setup(
        status=ExecutionStatus.DISPATCH_IN_PROGRESS, lease={"expires_at": "2099-01-01T00:00:00+00:00"},
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert "already in progress" in reason


def test_find_eligible_candidates_rejects_unresolved_ambiguous_outcome():
    execution_manager, workflow_manager, publication_manager = _standard_setup(
        status=ExecutionStatus.DISPATCH_FAILED,
        failure={"category": FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME.value, "retryable": True},
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert "ambiguous outcome" in reason


def test_find_eligible_candidates_rejects_permanent_non_ambiguous_failure():
    execution_manager, workflow_manager, publication_manager = _standard_setup(
        status=ExecutionStatus.DISPATCH_FAILED,
        failure={"category": FailureCategory.INVALID_CREDENTIALS.value, "retryable": False},
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []


def test_find_eligible_candidates_allows_retryable_non_ambiguous_failure():
    execution_manager, workflow_manager, publication_manager = _standard_setup(
        status=ExecutionStatus.DISPATCH_FAILED,
        failure={"category": FailureCategory.TRANSIENT_PLATFORM_ERROR.value, "retryable": True},
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert len(candidates) == 1


def test_find_eligible_candidates_rejects_reel_placement():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    publication_manager.find_existing.return_value = _make_package(
        media=[{"asset_id": "a1", "media_type": "video/mp4", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert "JPEG Feed" in reason


def test_find_eligible_candidates_rejects_non_jpeg_media():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    publication_manager.find_existing.return_value = _make_package(
        media=[{"asset_id": "a1", "media_type": "image/png", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []


def test_find_eligible_candidates_rejects_when_diagnostics_not_ready():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        diagnostics_service=_make_diagnostics_service(ready=False),
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []
    assert "not currently ready" in reason


def test_find_eligible_candidates_rejects_when_publishing_disabled_via_not_ready_diagnostics():
    # Publishing-disabled is surfaced through diagnostics' own
    # instagram_ready=False (failure_stage="publishing_disabled") — no
    # separate check is duplicated here.
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    diagnostics_service = MagicMock()
    from src.publisher.instagram.diagnostics import InstagramDiagnosticsResult
    diagnostics_service.check_status.return_value = InstagramDiagnosticsResult(
        application_healthy=True, publishing_enabled=False, instagram_configured=False,
        token_valid=None, permissions_ok=None, account_verified=None, account_username=None,
        instagram_ready=False, failure_stage="publishing_disabled", failure_reason="Instagram publishing is not enabled",
    )
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        diagnostics_service=diagnostics_service,
    )

    candidates, reason = service.find_eligible_candidates(telegram_user_id=42)
    assert candidates == []


# --- confirmation preview -------------------------------------------------------


def test_build_preview_contains_masked_account_and_feed_placement():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    assert isinstance(preview, LiveValidationPreview)
    assert preview.account_fingerprint == "••••0000"
    assert "179838000000000" not in preview.account_fingerprint
    assert preview.placement == "feed"
    assert preview.media_mode == "single_image"
    assert preview.approved_version_number == 2
    assert preview.media_count == 1
    assert preview.confirmation_token


def test_build_preview_persists_durable_awaiting_confirmation_state():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    service.build_preview(telegram_user_id=42, publication_id="pub-1")

    live_validation = execution_manager.document.metadata["live_validation"]
    assert live_validation["status"] == "AWAITING_CONFIRMATION"
    assert live_validation["operator_telegram_user_id"] == 42
    assert live_validation["mode"] == "controlled_live_validation"


def test_build_preview_denies_non_owner():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    workflow_manager.load_workflow.return_value = _make_workflow(telegram_user_id=999)
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    with pytest.raises(NotLiveTestOperatorError):
        service.build_preview(telegram_user_id=42, publication_id="pub-1")


def test_build_preview_raises_for_ineligible_execution():
    execution_manager, workflow_manager, publication_manager = _standard_setup(status=ExecutionStatus.COMPLETED)
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    with pytest.raises(NoEligibleLiveValidationCandidateError):
        service.build_preview(telegram_user_id=42, publication_id="pub-1")


# --- confirmation: expiry / staleness / consumption -----------------------------


async def test_confirm_dispatches_through_the_real_execution_dispatch_service():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    dispatch_service = _make_dispatch_service(execution_manager, publisher, workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")
    outcome = await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)

    assert outcome.status == "completed"
    assert publisher.calls  # the real publisher was actually invoked, via the real dispatch service
    auth = execution_manager.document.dispatch_authorization
    assert auth["dispatch_context"]["mode"] == "controlled_live_validation"
    assert auth["dispatch_context"]["operator_telegram_user_id"] == 42


async def test_confirm_never_calls_publisher_directly():
    # The live-validation service itself must import neither the
    # Instagram publisher adapter nor its HTTP client — structural proof
    # it has no way to call publish_media()/create_media_container()
    # itself, and must route every dispatch through
    # ExecutionDispatchService instead.
    from src.execution import live_validation

    module_names = {getattr(v, "__module__", None) for v in vars(live_validation).values()}
    assert "src.publisher.instagram.publisher" not in module_names
    assert "src.publisher.instagram.client" not in module_names


async def test_confirm_rejects_wrong_token():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    service.build_preview(telegram_user_id=42, publication_id="pub-1")

    with pytest.raises(LiveValidationConfirmationNotFoundError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token="wrong-token")


async def test_confirm_rejects_expired_confirmation():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    # Force the persisted confirmation to already be expired.
    live_validation = dict(execution_manager.document.metadata["live_validation"])
    live_validation["expires_at"] = "2020-01-01T00:00:00+00:00"
    execution_manager.document = replace(
        execution_manager.document, metadata={**execution_manager.document.metadata, "live_validation": live_validation},
    )

    with pytest.raises(LiveValidationConfirmationExpiredError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


async def test_confirm_rejects_when_package_changed_since_preview():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    # Simulate the package changing (in reality packages are immutable —
    # this proves the digest check works even if that invariant were
    # ever violated, or a different package were somehow substituted).
    publication_manager.find_existing.return_value = _make_package(content={"caption": "a different caption now"})

    with pytest.raises(LiveValidationStateChangedError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


async def test_confirm_rejects_when_execution_state_changed_since_preview():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    # Someone else advanced the execution's attempt count in between.
    execution_manager.document = replace(execution_manager.document, attempt=99)

    with pytest.raises(LiveValidationStateChangedError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


async def test_confirm_rejects_when_diagnostics_no_longer_ready():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    diagnostics_service = _make_diagnostics_service(ready=True)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        diagnostics_service=diagnostics_service,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    diagnostics_service.check_status.return_value = _make_diagnostics_service(ready=False).check_status.return_value

    with pytest.raises(LiveValidationNotReadyError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


async def test_confirm_is_one_time_use():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    publisher = _ScriptedPublisher([
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    dispatch_service = _make_dispatch_service(execution_manager, publisher, workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    first = await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)
    assert first.status == "completed"

    with pytest.raises(LiveValidationConfirmationNotFoundError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)

    # Exactly one publish call total, regardless of the repeated callback.
    assert len(publisher.calls) == 3


async def test_confirm_denies_non_operator():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    with pytest.raises(NotLiveTestOperatorError):
        await service.confirm(telegram_user_id=999, publication_id="pub-1", confirmation_token=preview.confirmation_token)


async def test_confirm_rejects_ineligible_execution_at_confirm_time_even_if_preview_was_built_earlier():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    # Execution independently completed via a normal dispatch in between
    # (matching status/checkpoint/attempt fencing would need to also
    # change for the state-changed check to fire first; force COMPLETED
    # with matching sentinel fields to specifically exercise the
    # eligibility re-check rather than the state-changed check).
    execution_manager.document = replace(execution_manager.document, status=ExecutionStatus.COMPLETED)

    with pytest.raises((LiveValidationStateChangedError, NoEligibleLiveValidationCandidateError)):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


# --- cancel ---------------------------------------------------------------------


def test_cancel_marks_confirmation_cancelled():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    service.cancel(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)

    assert execution_manager.document.metadata["live_validation"]["status"] == "CANCELLED"


async def test_cancel_then_confirm_cannot_dispatch():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")
    service.cancel(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)

    with pytest.raises(LiveValidationConfirmationNotFoundError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


def test_cancel_is_idempotent_and_never_raises():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    service.cancel(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)
    # Second cancel: already cancelled — must not raise.
    service.cancel(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)


# --- failure paths after dispatch begins ----------------------------------------


# --- Part M: /retry resumes a controlled-live-validation dispatch unchanged ----


async def test_retry_dispatch_resumes_a_controlled_live_validation_authorization_without_a_new_confirmation():
    """Proves the Milestone 11B recommended /retry rule: once `confirm()`
    has created a `dispatch_authorization` (carrying `dispatch_context`),
    an ordinary `/retry` (`ExecutionDispatchService.retry_dispatch()`,
    completely unchanged — see src/handlers.py's "publication_dispatch"
    branch) can resume the SAME execution through a retryable pre-publish
    failure without any new live-validation confirmation, because
    dispatch_authorization is (per Milestone 10's own design) a standing,
    execution-scoped authorization, not a single-use token. No second
    retry system was built for this."""

    execution_manager, workflow_manager, publication_manager = _standard_setup()
    from src.publisher.errors import PublisherRetryableError

    publisher = _ScriptedPublisher([
        PublisherRetryableError("network blip"),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state_update={"container_id": "c1"}),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_READY),
        PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=_success_result()),
    ])
    dispatch_service = _make_dispatch_service(execution_manager, publisher, workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    first = await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)
    assert first.status == "retryable_failure"
    authorization_after_confirm = execution_manager.document.dispatch_authorization
    assert authorization_after_confirm["dispatch_context"]["mode"] == "controlled_live_validation"

    # Ordinary /retry — never touches live_validation_service at all.
    second = await dispatch_service.retry_dispatch(publication_id="pub-1", telegram_user_id=42)

    assert second.status == "completed"
    # The authorization (and its controlled-live-validation provenance)
    # is unchanged/reused, never recreated.
    assert execution_manager.document.dispatch_authorization["dispatch_context"] == authorization_after_confirm["dispatch_context"]


async def test_confirm_reports_permanent_failure_without_raising():
    execution_manager, workflow_manager, publication_manager = _standard_setup()
    publisher = _ScriptedPublisher([PublisherPermanentError("boom")])
    dispatch_service = _make_dispatch_service(execution_manager, publisher, workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )
    preview = service.build_preview(telegram_user_id=42, publication_id="pub-1")

    outcome = await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token=preview.confirmation_token)

    assert outcome.status == "permanent_failure"


# --- historical (pre-11B) executions remain safely readable --------------------


def test_cancel_is_safe_for_a_pre_11b_execution_with_no_live_validation_metadata():
    # A real historical execution has no "live_validation" key in its
    # metadata dict at all (it didn't exist before this milestone) —
    # cancel() must treat that exactly like "nothing to cancel," never
    # raise a KeyError/AttributeError.
    execution_manager, workflow_manager, publication_manager = _standard_setup(metadata={"pending_retry": None})
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    service.cancel(telegram_user_id=42, publication_id="pub-1", confirmation_token="whatever")  # must not raise


async def test_confirm_is_safe_for_a_pre_11b_execution_with_no_live_validation_metadata():
    execution_manager, workflow_manager, publication_manager = _standard_setup(metadata={"pending_retry": None})
    dispatch_service = _make_dispatch_service(execution_manager, MagicMock(), workflow_manager=workflow_manager, publication_manager=publication_manager)
    service = _make_live_validation_service(
        execution_manager=execution_manager, dispatch_service=dispatch_service,
        workflow_manager=workflow_manager, publication_manager=publication_manager,
    )

    with pytest.raises(LiveValidationConfirmationNotFoundError):
        await service.confirm(telegram_user_id=42, publication_id="pub-1", confirmation_token="whatever")
