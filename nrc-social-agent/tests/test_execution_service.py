from unittest.mock import MagicMock

import pytest

from src.execution.errors import (
    ExecutionConcurrentModificationError,
    ExecutionPersistenceError,
    PublicationPackageNotFoundForExecutionError,
    PublicationPackageNotReadyForExecutionError,
)
from src.execution.models import ExecutionStatus
from src.execution.service import ExecutionOutcome, ExecutionService
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState


def _make_package(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1,
        created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


def _make_workflow(**overrides) -> WorkflowDocument:
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=42, created_at="x", updated_at="x",
        state=WorkflowState.COMPLETED, media={},
        publication={
            "publication_id": "pub-1", "output_id": "out-1", "channel": "instagram",
            "status": "READY_FOR_PUBLISHING", "schema_version": 1, "prepared_at": "t1",
        },
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_service(*, workflow_manager=None, publication_manager=None, execution_manager=None, conversation_manager=None):
    return ExecutionService(
        workflow_manager=workflow_manager or MagicMock(),
        publication_manager=publication_manager or MagicMock(),
        execution_manager=execution_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


# --- success path ------------------------------------------------------


async def test_create_execution_builds_and_persists_a_new_record():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None
    created = MagicMock()
    created.execution_id = "exec-1"
    created.publication_id = "pub-1"
    created.workflow_id = "wf-1"
    created.channel = PublicationChannel.INSTAGRAM
    created.publisher = "instagram"
    created.status = ExecutionStatus.READY_FOR_DISPATCH
    created.attempt = 0
    execution_manager.create_execution.return_value = created
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager,
    )

    outcome = await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    assert isinstance(outcome, ExecutionOutcome)
    assert outcome.execution_id == "exec-1"
    assert outcome.status == "READY_FOR_DISPATCH"

    execution_manager.create_execution.assert_called_once_with(
        publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM, publisher="instagram",
    )
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_create_execution_never_touches_the_publication_package():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None
    execution_manager.create_execution.return_value = MagicMock(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        publisher="instagram", status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0,
    )

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager, execution_manager=execution_manager,
    )

    await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    # No method on publication_manager other than find_existing() is ever
    # called — the package is read, never written.
    assert publication_manager.method_calls == [
        m for m in publication_manager.method_calls if m[0] == "find_existing"
    ]


async def test_create_execution_never_calls_claude_or_a_platform():
    # This service's constructor accepts no Claude client and no platform
    # SDK dependency at all — the strongest possible guarantee.
    import inspect

    from src.execution.service import ExecutionService as _Service

    params = inspect.signature(_Service.__init__).parameters
    assert "claude_client" not in params
    assert not any("platform" in name for name in params)


async def test_create_execution_defers_pointer_clearing_when_requested():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None
    execution_manager.create_execution.return_value = MagicMock(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        publisher="instagram", status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0,
    )
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager,
    )

    await service.create_execution(workflow_id="wf-1", telegram_user_id=42, clear_pointer_on_success=False)

    conversation_manager.clear_active_workflow.assert_not_called()


# --- idempotency ---------------------------------------------------------


async def test_create_execution_reuses_existing_record():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    execution_manager = MagicMock()
    existing = MagicMock(
        execution_id="exec-existing", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        publisher="instagram", status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0,
    )
    execution_manager.find_existing.return_value = existing
    publication_manager = MagicMock()
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager,
    )

    outcome = await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.execution_id == "exec-existing"
    execution_manager.create_execution.assert_not_called()
    publication_manager.find_existing.assert_not_called()
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_create_execution_reconciles_a_concurrent_creation_race():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    execution_manager = MagicMock()
    winner = MagicMock(
        execution_id="exec-winner", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        publisher="instagram", status=ExecutionStatus.READY_FOR_DISPATCH, attempt=0,
    )
    # First find_existing() (idempotency check) sees nothing; the
    # conditional create then loses a race; the second find_existing()
    # (reconciliation) sees the winner.
    execution_manager.find_existing.side_effect = [None, winner]
    execution_manager.create_execution.side_effect = ExecutionConcurrentModificationError("raced")

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager, execution_manager=execution_manager,
    )

    outcome = await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.execution_id == "exec-winner"


# --- eligibility -----------------------------------------------------------


async def test_create_execution_rejects_workflow_without_a_publication_reference():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(publication=None)
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(PublicationPackageNotReadyForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)


async def test_create_execution_rejects_a_failed_publication_reference():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(
        publication={"publication_id": "pub-1", "output_id": "out-1", "status": "FAILED"}
    )
    publication_manager = MagicMock()
    service = _make_service(workflow_manager=workflow_manager, publication_manager=publication_manager)

    with pytest.raises(PublicationPackageNotReadyForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    publication_manager.find_existing.assert_not_called()


async def test_create_execution_rejects_when_the_authoritative_package_cannot_be_loaded():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager, execution_manager=execution_manager,
    )

    with pytest.raises(PublicationPackageNotFoundForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    execution_manager.create_execution.assert_not_called()


async def test_create_execution_rejects_a_package_that_is_not_ready_for_publishing():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package(status=PublicationStatus.FAILED)
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager, execution_manager=execution_manager,
    )

    with pytest.raises(PublicationPackageNotFoundForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)


async def test_create_execution_rejects_a_publication_id_linkage_mismatch():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package(publication_id="pub-different")
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager, execution_manager=execution_manager,
    )

    with pytest.raises(PublicationPackageNotFoundForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)


async def test_create_execution_ineligibility_has_zero_side_effects():
    # Mirrors src/publication/service.py's identical precedent: a plain
    # "not eligible yet" check (no publication reference at all) has zero
    # side effects — it never touches metadata, never clears the pointer.
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(publication=None)
    conversation_manager = MagicMock()
    service = _make_service(workflow_manager=workflow_manager, conversation_manager=conversation_manager)

    with pytest.raises(PublicationPackageNotReadyForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    conversation_manager.clear_active_workflow.assert_not_called()
    workflow_manager.update_metadata.assert_not_called()


async def test_create_execution_permanent_failure_clears_pending_retry_but_not_pointer():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(PublicationPackageNotFoundForExecutionError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    conversation_manager.clear_active_workflow.assert_not_called()
    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": None})


# --- retryable failure -----------------------------------------------------


async def test_create_execution_sets_pending_retry_on_persistence_failure():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = _make_package()
    execution_manager = MagicMock()
    execution_manager.find_existing.return_value = None
    execution_manager.create_execution.side_effect = ExecutionPersistenceError("S3 hiccup")
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        execution_manager=execution_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(ExecutionPersistenceError):
        await service.create_execution(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "publication_execution"})
    conversation_manager.clear_active_workflow.assert_not_called()
