import json
from unittest.mock import MagicMock

import pytest

from src.ai.client import ClaudeAnalysisResponse
from src.ai.content_planning_service import ContentPlanOutcome, ContentPlanningService
from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus
from src.ai.errors import (
    ClaudeAuthenticationError,
    ClaudeTimeoutError,
    ContentPlanValidationFailedError,
    MissingAnalysisForPlanningError,
    WorkflowNotEligibleForPlanningError,
)
from src.ai.content_plan_models import PlannedOutput
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

_EMPTY_UPDATES = {
    "brand_name": "", "content_type": "", "objective": "", "audience": "", "message_focus": "",
    "tone": "", "call_to_action": "", "platforms": [], "factual_context": {}, "user_preferences": {},
}

VALID_STRATEGY = {
    "content_type": "founder_introduction",
    "content_type_description": "",
    "primary_objective": "build founder credibility for NRC",
    "supporting_objective": "",
    "audience": ["business owners"],
    "central_message": "The founder's on-camera introduction should carry premium credibility forward",
    "brand_positioning": "premium, connected",
    "tone_direction": ["confident", "premium"],
    "cta_direction": "invite viewers to explore NRC",
    "factual_constraints": ["brand: NRC"],
    "avoid": ["performance claims"],
}

VALID_OUTPUT = {
    "output_type": "instagram_reel_caption",
    "priority": 1,
    "purpose": "build founder credibility on Instagram",
    "audience": ["business owners"],
    "message_focus": "make the NRC founder story immediate and memorable",
    "tone": ["confident"],
    "cta_direction": "invite viewers to explore NRC",
    "required_context": [],
    "constraints": [],
}


def _plan_payload(**overrides):
    payload = {"strategy": VALID_STRATEGY, "outputs": [VALID_OUTPUT], "excluded_outputs": []}
    payload.update(overrides)
    return payload


def _claude_response(payload):
    return ClaudeAnalysisResponse(
        text=json.dumps(payload), input_tokens=10, output_tokens=5, model="claude-opus-5", duration_seconds=0.2
    )


def _make_workflow(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.GENERATING_CONTENT,
        media={"media_type": "photo"},
        conversation=[],
        metadata={},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_analysis(summary="A founder speaking to camera in a premium setting."):
    return AnalysisDocument(
        analysis_id="an-1", workflow_id="wf-1", created_at="x", updated_at="x", status=AnalysisStatus.COMPLETED,
        schema_version=1, prompt_version=1, model="claude-opus-5", media_type="photo",
        result=AnalysisResult(
            summary=summary, visible_subjects=["a person"], visual_style=["natural light"],
            dominant_themes=["introduction"], brand_signals=[], content_opportunities=["founder story"],
            quality_observations=[], safety_notes=[],
        ),
        usage=AnalysisUsage(input_tokens=1, output_tokens=1),
    )


def _make_content_plan_document(**overrides):
    defaults = dict(
        plan_id="plan-1", workflow_id="wf-1", created_at="x", updated_at="x", status=ContentPlanStatus.FAILED,
        schema_version=1, prompt_version=1, model="claude-opus-5",
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


def _make_service(
    *, claude_client=None, analysis_manager=None, content_plan_manager=None, workflow_manager=None,
    conversation_manager=None, max_outputs=3,
):
    return ContentPlanningService(
        model="claude-opus-5", schema_version=1, max_outputs=max_outputs,
        claude_client=claude_client or MagicMock(),
        analysis_manager=analysis_manager or MagicMock(),
        content_plan_manager=content_plan_manager or MagicMock(),
        workflow_manager=workflow_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


# --- success path ------------------------------------------------------


async def test_plan_content_persists_and_attaches_reference():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow

    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    persisted = _make_content_plan_document(
        status=ContentPlanStatus.COMPLETED,
        outputs=[PlannedOutput(output_id="out-1", output_type="instagram_reel_caption", priority=1, purpose="x", message_focus="y", cta_direction="z")],
    )
    content_plan_manager.create_content_plan.return_value = persisted

    claude_client = MagicMock()
    claude_client.plan_content.return_value = _claude_response(_plan_payload())

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    assert isinstance(outcome, ContentPlanOutcome)
    assert outcome.plan_id == persisted.plan_id
    assert len(outcome.outputs) == 1
    assert outcome.outputs[0].output_type == "instagram_reel_caption"
    assert outcome.outputs[0].output_id != "pending"  # service-assigned

    content_plan_manager.create_content_plan.assert_called_once()
    _, kwargs = content_plan_manager.create_content_plan.call_args
    assert kwargs["status"] is ContentPlanStatus.COMPLETED

    workflow_manager.attach_content_plan_reference.assert_called_once()
    wf_id, reference = workflow_manager.attach_content_plan_reference.call_args.args
    assert wf_id == "wf-1"
    assert reference["primary_output_type"] == "instagram_reel_caption"
    assert reference["output_count"] == 1

    # Success must not touch workflow state or the active pointer.
    workflow_manager.update_state.assert_not_called()


async def test_plan_content_sends_a_prompt_grounded_in_the_persisted_analysis():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis(summary="A premium founder-led intro video.")
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    content_plan_manager.create_content_plan.return_value = _make_content_plan_document(status=ContentPlanStatus.COMPLETED)
    claude_client = MagicMock()
    claude_client.plan_content.return_value = _claude_response(_plan_payload())

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = claude_client.plan_content.call_args
    assert "A premium founder-led intro video." in kwargs["user_prompt"]


# --- idempotency --------------------------------------------------------


async def test_plan_content_reuses_existing_completed_plan_without_calling_claude():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow

    existing = _make_content_plan_document(status=ContentPlanStatus.COMPLETED)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = existing

    claude_client = MagicMock()
    analysis_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.plan_id == existing.plan_id
    claude_client.plan_content.assert_not_called()
    analysis_manager.find_existing.assert_not_called()
    content_plan_manager.create_content_plan.assert_not_called()
    workflow_manager.attach_content_plan_reference.assert_not_called()


async def test_plan_content_retries_a_prior_failed_plan_via_supersede():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow

    existing = _make_content_plan_document(status=ContentPlanStatus.FAILED)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = existing
    content_plan_manager.supersede_failed_content_plan.return_value = _make_content_plan_document(
        status=ContentPlanStatus.COMPLETED
    )

    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.plan_content.return_value = _claude_response(_plan_payload())

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    content_plan_manager.supersede_failed_content_plan.assert_called_once()
    content_plan_manager.create_content_plan.assert_not_called()


# --- eligibility (no side effects on failure) -----------------------------


async def test_plan_content_rejects_ineligible_workflow_state_without_side_effects():
    workflow = _make_workflow(state=WorkflowState.WAITING_FOR_USER)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, workflow_manager=workflow_manager
    )

    with pytest.raises(WorkflowNotEligibleForPlanningError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    claude_client.plan_content.assert_not_called()
    content_plan_manager.find_existing.assert_not_called()
    content_plan_manager.create_content_plan.assert_not_called()
    workflow_manager.update_state.assert_not_called()


async def test_plan_content_rejects_missing_analysis_without_side_effects():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = None
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(MissingAnalysisForPlanningError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    claude_client.plan_content.assert_not_called()
    workflow_manager.update_state.assert_not_called()


async def test_plan_content_propagates_workflow_load_failure():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = WorkflowPersistenceError("s3 down")
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(Exception):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)


# --- retryable vs permanent failures ---------------------------------------


async def test_plan_content_sets_pending_retry_on_retryable_failure_without_touching_state():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.plan_content.side_effect = ClaudeTimeoutError("timed out")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeTimeoutError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "content_planning"})
    workflow_manager.update_state.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()
    content_plan_manager.create_content_plan.assert_called_once()  # FAILED record persisted


async def test_plan_content_marks_failed_and_clears_pointer_on_permanent_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.plan_content.side_effect = ClaudeAuthenticationError("bad key")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeAuthenticationError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


# --- validation and one bounded regeneration attempt -----------------------


async def test_plan_content_regenerates_once_after_a_validation_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    content_plan_manager.create_content_plan.return_value = _make_content_plan_document(status=ContentPlanStatus.COMPLETED)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    generic_payload = _plan_payload(
        strategy={**VALID_STRATEGY, "central_message": "increase engagement"}
    )
    good_payload = _plan_payload()
    claude_client = MagicMock()
    claude_client.plan_content.side_effect = [_claude_response(generic_payload), _claude_response(good_payload)]

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.plan_content.call_count == 2
    assert outcome.outputs[0].output_type == "instagram_reel_caption"


async def test_plan_content_fails_safely_when_regeneration_also_invalid():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    generic_payload = _plan_payload(strategy={**VALID_STRATEGY, "central_message": "increase engagement"})
    claude_client = MagicMock()
    claude_client.plan_content.side_effect = [_claude_response(generic_payload), _claude_response(generic_payload)]
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ContentPlanValidationFailedError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.plan_content.call_count == 2
    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_plan_content_rejects_a_plan_exceeding_max_outputs():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    too_many_outputs = _plan_payload(
        outputs=[
            VALID_OUTPUT,
            {**VALID_OUTPUT, "output_type": "linkedin_post", "priority": 2, "message_focus": "a distinct LinkedIn angle for NRC founders"},
            {**VALID_OUTPUT, "output_type": "threads_post", "priority": 3, "message_focus": "a distinct Threads angle for NRC founders"},
        ]
    )
    claude_client = MagicMock()
    claude_client.plan_content.side_effect = [_claude_response(too_many_outputs), _claude_response(too_many_outputs)]
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager, max_outputs=2,
    )

    with pytest.raises(ContentPlanValidationFailedError):
        await service.plan_content(workflow_id="wf-1", telegram_user_id=42)


# --- output assignment ----------------------------------------------------


async def test_plan_content_assigns_unique_output_ids_and_sorts_by_priority():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    content_plan_manager.create_content_plan.return_value = _make_content_plan_document(status=ContentPlanStatus.COMPLETED)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    multi_output_payload = _plan_payload(
        outputs=[
            {**VALID_OUTPUT, "output_type": "linkedin_post", "priority": 2, "message_focus": "explain the NRC founder's reasoning for LinkedIn"},
            VALID_OUTPUT,  # priority 1
        ]
    )
    claude_client = MagicMock()
    claude_client.plan_content.return_value = _claude_response(multi_output_payload)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager,
        content_plan_manager=content_plan_manager, workflow_manager=workflow_manager, max_outputs=3,
    )

    outcome = await service.plan_content(workflow_id="wf-1", telegram_user_id=42)

    assert [o.priority for o in outcome.outputs] == [1, 2]
    assert outcome.outputs[0].output_type == "instagram_reel_caption"
    ids = [o.output_id for o in outcome.outputs]
    assert len(set(ids)) == len(ids)
