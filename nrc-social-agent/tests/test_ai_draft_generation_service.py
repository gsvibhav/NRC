import json
from unittest.mock import MagicMock

import pytest

from src.ai.client import ClaudeAnalysisResponse
from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus, PlanStrategy, PlannedOutput
from src.ai.draft_generation_service import DraftGenerationOutcome, DraftGenerationService
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.errors import (
    ClaudeAuthenticationError,
    ClaudeTimeoutError,
    DraftValidationFailedError,
    MissingAnalysisForPlanningError,
    MissingContentPlanForGenerationError,
    NoPrimaryOutputError,
    UnsupportedGenerationOutputTypeError,
    WorkflowNotEligibleForDraftGenerationError,
)
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

VALID_STRATEGY = PlanStrategy(
    content_type="founder_introduction",
    primary_objective="build founder credibility for NRC",
    central_message="The founder's on-camera introduction should carry premium credibility forward",
    brand_positioning="premium, connected",
    cta_direction="invite viewers to explore NRC",
    audience=["business owners"],
    tone_direction=["confident", "premium"],
    factual_constraints=["brand: NRC"],
    avoid=["performance claims"],
)


def _output(**overrides):
    defaults = dict(
        output_id="out-1",
        output_type="instagram_reel_caption",
        priority=1,
        purpose="build founder credibility on Instagram",
        message_focus="make the NRC founder story immediate and memorable",
        cta_direction="invite viewers to explore NRC",
        audience=["business owners"],
        tone=["confident"],
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


GOOD_CAPTION_PAYLOAD = {
    "caption": "The founder's on-camera introduction carries premium credibility forward for NRC.",
    "hashtags": [],
    "cta": "",
}

GENERIC_CAPTION_PAYLOAD = {
    "caption": "We are excited to share our journey with you today on this platform.",
    "hashtags": [],
    "cta": "",
}


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


def _make_plan(**overrides):
    defaults = dict(
        plan_id="plan-1", workflow_id="wf-1", created_at="x", updated_at="x", status=ContentPlanStatus.COMPLETED,
        schema_version=1, prompt_version=1, model="claude-opus-5", strategy=VALID_STRATEGY, outputs=[_output()],
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


def _make_draft_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.FAILED, schema_version=1, prompt_version=1, model="claude-opus-5",
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def _make_service(
    *, claude_client=None, analysis_manager=None, content_plan_manager=None, draft_manager=None,
    workflow_manager=None, conversation_manager=None,
):
    return DraftGenerationService(
        model="claude-opus-5", schema_version=1, max_instagram_caption_length=2200, max_hashtags=5,
        claude_client=claude_client or MagicMock(),
        analysis_manager=analysis_manager or MagicMock(),
        content_plan_manager=content_plan_manager or MagicMock(),
        draft_manager=draft_manager or MagicMock(),
        workflow_manager=workflow_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


# --- success path ------------------------------------------------------


async def test_generate_draft_persists_attaches_reference_and_transitions_state():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow

    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()

    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    persisted = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW, content=GOOD_CAPTION_PAYLOAD)
    draft_manager.create_draft.return_value = persisted

    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    claude_client = MagicMock()
    claude_client.generate_draft.return_value = _claude_response(GOOD_CAPTION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert isinstance(outcome, DraftGenerationOutcome)
    assert outcome.draft_id == persisted.draft_id
    assert outcome.output_id == "out-1"
    assert outcome.output_type == "instagram_reel_caption"

    draft_manager.create_draft.assert_called_once()
    _, kwargs = draft_manager.create_draft.call_args
    assert kwargs["status"] is DraftStatus.READY_FOR_REVIEW
    assert kwargs["plan_id"] == "plan-1"
    assert kwargs["output_id"] == "out-1"

    workflow_manager.attach_generated_draft_reference.assert_called_once()
    (wf_id, reference), attach_kwargs = workflow_manager.attach_generated_draft_reference.call_args
    assert wf_id == "wf-1"
    assert reference["output_id"] == "out-1"
    assert attach_kwargs["new_state"] is WorkflowState.SHOWING_PREVIEW

    content_plan_manager.mark_output_generated.assert_called_once_with(workflow_id="wf-1", output_id="out-1")
    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": None})
    workflow_manager.update_state.assert_not_called()  # only attach_generated_draft_reference transitions state


async def test_generate_draft_records_source_versions():
    workflow = _make_workflow(clarification_context={"version": 2, "brand_name": None})
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(schema_version=1)
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    draft_manager.create_draft.return_value = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.return_value = _claude_response(GOOD_CAPTION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = draft_manager.create_draft.call_args
    source_versions = kwargs["source_versions"]
    assert source_versions["analysis_schema_version"] == 1
    assert source_versions["content_plan_schema_version"] == 1
    assert source_versions["clarification_context_version"] == 2


async def test_generate_draft_sends_a_prompt_grounded_in_the_persisted_plan_and_analysis():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    draft_manager.create_draft.return_value = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis(summary="A premium founder-led intro video.")
    claude_client = MagicMock()
    claude_client.generate_draft.return_value = _claude_response(GOOD_CAPTION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = claude_client.generate_draft.call_args
    assert "A premium founder-led intro video." in kwargs["user_prompt"]
    assert "instagram_reel_caption" in kwargs["user_prompt"]
    # Never mention a secondary/excluded output type — only the selected one.
    assert "linkedin_post" not in kwargs["user_prompt"]


# --- primary output selection --------------------------------------------


async def test_generate_draft_raises_when_no_priority_one_output_exists():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(outputs=[_output(priority=2)])
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(NoPrimaryOutputError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()


async def test_generate_draft_raises_when_multiple_priority_one_outputs_exist():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(
        outputs=[_output(output_id="out-1"), _output(output_id="out-2", output_type="linkedin_post")]
    )
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(NoPrimaryOutputError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()


async def test_generate_draft_never_selects_a_secondary_output():
    # Even when a plan has multiple outputs, the priority-1 one must be
    # the only one ever generated (this milestone's critical rule).
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(
        outputs=[
            _output(output_id="out-1", output_type="instagram_reel_caption", priority=1),
            _output(output_id="out-2", output_type="linkedin_post", priority=2),
        ]
    )
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    draft_manager.create_draft.return_value = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.return_value = _claude_response(GOOD_CAPTION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.output_id == "out-1"
    _, kwargs = draft_manager.create_draft.call_args
    assert kwargs["output_id"] == "out-1"
    assert kwargs["output_type"] == "instagram_reel_caption"


# --- unsupported output type ----------------------------------------------


async def test_generate_draft_raises_for_unsupported_output_type_without_calling_claude():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(
        outputs=[_output(output_type="linkedin_post")]
    )
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, draft_manager=draft_manager,
        workflow_manager=workflow_manager,
    )

    with pytest.raises(UnsupportedGenerationOutputTypeError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()


# --- idempotency --------------------------------------------------------


async def test_generate_draft_reuses_existing_ready_draft_without_calling_claude():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    existing_draft = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW, content=GOOD_CAPTION_PAYLOAD)
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = existing_draft
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, draft_manager=draft_manager,
        workflow_manager=workflow_manager,
    )

    outcome = await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.draft_id == existing_draft.draft_id
    claude_client.generate_draft.assert_not_called()
    draft_manager.create_draft.assert_not_called()
    workflow_manager.attach_generated_draft_reference.assert_not_called()


async def test_generate_draft_idempotent_replay_works_even_when_already_in_showing_preview():
    # A duplicate trigger after success sees SHOWING_PREVIEW, not
    # GENERATING_CONTENT — idempotency must still short-circuit before any
    # state-eligibility check.
    workflow = _make_workflow(state=WorkflowState.SHOWING_PREVIEW)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    existing_draft = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW, content=GOOD_CAPTION_PAYLOAD)
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = existing_draft
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, draft_manager=draft_manager,
        workflow_manager=workflow_manager,
    )

    outcome = await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.draft_id == existing_draft.draft_id
    claude_client.generate_draft.assert_not_called()


async def test_generate_draft_retries_a_prior_failed_draft_via_supersede():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    existing_failed = _make_draft_document(status=DraftStatus.FAILED)
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = existing_failed
    draft_manager.supersede_failed_draft.return_value = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.return_value = _claude_response(GOOD_CAPTION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    draft_manager.supersede_failed_draft.assert_called_once()
    draft_manager.create_draft.assert_not_called()


# --- eligibility (no side effects on failure) -----------------------------


async def test_generate_draft_rejects_missing_content_plan_without_side_effects():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    claude_client = MagicMock()
    draft_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, draft_manager=draft_manager,
        workflow_manager=workflow_manager,
    )

    with pytest.raises(MissingContentPlanForGenerationError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()
    draft_manager.find_existing.assert_not_called()
    workflow_manager.update_state.assert_not_called()


async def test_generate_draft_rejects_ineligible_workflow_state_without_side_effects():
    workflow = _make_workflow(state=WorkflowState.WAITING_FOR_USER)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager, draft_manager=draft_manager,
        workflow_manager=workflow_manager,
    )

    with pytest.raises(WorkflowNotEligibleForDraftGenerationError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()
    workflow_manager.update_state.assert_not_called()


async def test_generate_draft_rejects_missing_analysis_without_side_effects():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = None
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(MissingAnalysisForPlanningError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.generate_draft.assert_not_called()
    workflow_manager.update_state.assert_not_called()


async def test_generate_draft_propagates_workflow_load_failure():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = WorkflowPersistenceError("s3 down")
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(Exception):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)


# --- retryable vs permanent failures ---------------------------------------


async def test_generate_draft_sets_pending_retry_on_retryable_failure_without_touching_state():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.side_effect = ClaudeTimeoutError("timed out")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeTimeoutError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "primary_draft_generation"})
    workflow_manager.update_state.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()
    draft_manager.create_draft.assert_called_once()  # FAILED record persisted
    _, kwargs = draft_manager.create_draft.call_args
    assert kwargs["status"] is DraftStatus.FAILED


async def test_generate_draft_marks_failed_and_clears_pointer_on_permanent_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.side_effect = ClaudeAuthenticationError("bad key")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeAuthenticationError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


# --- validation and one bounded regeneration attempt -----------------------


async def test_generate_draft_regenerates_once_after_a_validation_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    draft_manager.create_draft.return_value = _make_draft_document(status=DraftStatus.READY_FOR_REVIEW)
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.side_effect = [
        _claude_response(GENERIC_CAPTION_PAYLOAD), _claude_response(GOOD_CAPTION_PAYLOAD),
    ]

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.generate_draft.call_count == 2
    assert outcome.output_type == "instagram_reel_caption"


async def test_generate_draft_fails_safely_when_regeneration_also_invalid():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    draft_manager = MagicMock()
    draft_manager.find_existing.return_value = None
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.generate_draft.side_effect = [
        _claude_response(GENERIC_CAPTION_PAYLOAD), _claude_response(GENERIC_CAPTION_PAYLOAD),
    ]
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_manager=draft_manager, workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(DraftValidationFailedError):
        await service.generate_draft(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.generate_draft.call_count == 2
    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)
