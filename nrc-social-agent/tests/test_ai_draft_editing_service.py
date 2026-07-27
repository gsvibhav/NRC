import json
from unittest.mock import MagicMock

import pytest

from src.ai.client import ClaudeAnalysisResponse
from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus, PlanStrategy, PlannedOutput
from src.ai.draft_editing_service import DraftEditingService, DraftEditOutcome
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.ai.errors import (
    ClaudeAuthenticationError,
    ClaudeTimeoutError,
    DraftVersionConflictError,
    MissingAnalysisForPlanningError,
    MissingContentPlanForGenerationError,
    NoPendingEditInstructionError,
    StaleDraftVersionError,
    UnsupportedEditRequestError,
    WorkflowNotEligibleForEditError,
)
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage
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
        output_id="out-1", output_type="instagram_reel_caption", priority=1,
        purpose="build founder credibility on Instagram",
        message_focus="make the NRC founder story immediate and memorable",
        cta_direction="invite viewers to explore NRC", audience=["business owners"], tone=["confident"],
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


GOOD_REVISION_PAYLOAD = {
    "caption": "The founder's on-camera introduction carries premium credibility forward for NRC, now shorter.",
    "hashtags": [], "cta": "",
}
GENERIC_REVISION_PAYLOAD = {"caption": "We are excited to share our journey with you today.", "hashtags": [], "cta": ""}


def _claude_response(payload):
    return ClaudeAnalysisResponse(
        text=json.dumps(payload), input_tokens=10, output_tokens=5, model="claude-opus-5", duration_seconds=0.2
    )


def _make_pending_edit(**overrides):
    defaults = dict(
        output_id="out-1", operation_id="op-1", expected_parent_version=1,
        status="INSTRUCTION_RECEIVED", instruction_turn_id="op-1", completed_version_number=None,
        requested_at="x",
    )
    defaults.update(overrides)
    return defaults


def _make_workflow(**overrides):
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=42, created_at="x", updated_at="x",
        state=WorkflowState.GENERATING_CONTENT, media={"media_type": "photo"},
        conversation=[{"role": "user", "content": "Make it more confident and premium.", "turn_id": "op-1"}],
        metadata={}, generated_draft={"output_id": "out-1"}, pending_edit=_make_pending_edit(),
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


def _make_current_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=1, prompt_version=1, model="claude-opus-5",
        content={"caption": "The founder's on-camera introduction carries premium credibility forward for NRC.",
                  "hashtags": [], "cta": None},
        version_number=1,
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def _make_current_pointer(**overrides):
    defaults = dict(
        workflow_id="wf-1", output_id="out-1", current_draft_id="draft-1", current_version_number=1,
        status=ReviewStatus.READY_FOR_REVIEW, updated_at="x",
    )
    defaults.update(overrides)
    return CurrentDraftPointer(**defaults)


def _make_service(
    *, claude_client=None, analysis_manager=None, content_plan_manager=None, draft_manager=None,
    draft_version_manager=None, workflow_manager=None, conversation_manager=None,
):
    return DraftEditingService(
        schema_version=1, max_instagram_caption_length=2200, max_hashtags=5,
        claude_client=claude_client or MagicMock(),
        analysis_manager=analysis_manager or MagicMock(),
        content_plan_manager=content_plan_manager or MagicMock(),
        draft_manager=draft_manager or MagicMock(),
        draft_version_manager=draft_version_manager or MagicMock(),
        workflow_manager=workflow_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


def _stub_current_draft(draft_version_manager, *, document=None, pointer=None, etag='"p-etag"'):
    draft_version_manager.get_current.return_value = (
        document or _make_current_document(), pointer or _make_current_pointer(), etag,
    )


# --- success path ------------------------------------------------------


async def test_edit_draft_creates_next_version_and_completes():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    new_version = _make_current_document(draft_id="draft-2", version_number=2, content=GOOD_REVISION_PAYLOAD)
    draft_version_manager.create_next_version.return_value = new_version
    claude_client = MagicMock()
    claude_client.edit_draft.return_value = _claude_response(GOOD_REVISION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    assert isinstance(outcome, DraftEditOutcome)
    assert outcome.version_number == 2
    assert outcome.draft_id == "draft-2"

    draft_version_manager.create_next_version.assert_called_once()
    _, kwargs = draft_version_manager.create_next_version.call_args
    assert kwargs["parent_version_number"] == 1
    assert kwargs["output_id"] == "out-1"
    assert kwargs["edit_instruction_reference"]["instruction_id"] == "op-1"

    workflow_manager.complete_edit.assert_called_once()
    _, kwargs = workflow_manager.complete_edit.call_args
    assert kwargs["generated_draft_reference"]["current_version"] == 2
    assert kwargs["pending_edit"]["status"] == "COMPLETED"
    assert kwargs["pending_edit"]["completed_version_number"] == 2


async def test_edit_draft_sends_a_prompt_grounded_in_current_content_and_instruction():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    draft_version_manager.create_next_version.return_value = _make_current_document(version_number=2)
    claude_client = MagicMock()
    claude_client.edit_draft.return_value = _claude_response(GOOD_REVISION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = claude_client.edit_draft.call_args
    assert "Make it more confident and premium." in kwargs["user_prompt"]
    assert "premium credibility forward for NRC" in kwargs["user_prompt"]


# --- idempotency --------------------------------------------------------


async def test_edit_draft_returns_existing_version_for_a_duplicate_completed_operation():
    completed_pending_edit = _make_pending_edit(status="COMPLETED", completed_version_number=2)
    workflow = _make_workflow(pending_edit=completed_pending_edit)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(
        draft_version_manager,
        document=_make_current_document(version_number=2, draft_id="draft-2"),
        pointer=_make_current_pointer(current_version_number=2),
    )
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    outcome = await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.version_number == 2
    assert outcome.draft_id == "draft-2"
    claude_client.edit_draft.assert_not_called()
    draft_version_manager.create_next_version.assert_not_called()


async def test_edit_draft_raises_when_completed_pending_edit_does_not_match_current_pointer():
    completed_pending_edit = _make_pending_edit(status="COMPLETED", completed_version_number=3)
    workflow = _make_workflow(pending_edit=completed_pending_edit)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager, pointer=_make_current_pointer(current_version_number=2))
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(NoPendingEditInstructionError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()


# --- eligibility / stale / no side effects --------------------------------


async def test_edit_draft_rejects_when_workflow_not_mid_edit():
    workflow = _make_workflow(state=WorkflowState.SHOWING_PREVIEW, pending_edit=None)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    claude_client = MagicMock()
    draft_version_manager = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(WorkflowNotEligibleForEditError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()
    draft_version_manager.get_current.assert_not_called()


async def test_edit_draft_rejects_stale_parent_version_without_calling_claude():
    workflow = _make_workflow(pending_edit=_make_pending_edit(expected_parent_version=1))
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager, pointer=_make_current_pointer(current_version_number=2))
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(StaleDraftVersionError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()
    workflow_manager.fail_edit_to_preview.assert_called_once_with("wf-1")


async def test_edit_draft_catches_up_bookkeeping_when_its_own_version_already_advanced_the_pointer():
    # Simulates a crash between create_next_version() succeeding and
    # complete_edit() catching up the workflow document: the pointer
    # already names version 2, and that version's own
    # edit_instruction_reference proves it came from THIS operation (same
    # operation_id) -- this must be recognized as "already done", not a
    # stale/foreign conflict, and must not call Claude again.
    workflow = _make_workflow(pending_edit=_make_pending_edit(operation_id="op-1", expected_parent_version=1))
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(
        draft_version_manager,
        document=_make_current_document(
            draft_id="draft-2", version_number=2, parent_version_number=1,
            edit_instruction_reference={"instruction_id": "op-1", "created_at": "x"},
        ),
        pointer=_make_current_pointer(current_version_number=2),
    )
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    outcome = await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.version_number == 2
    assert outcome.draft_id == "draft-2"
    claude_client.edit_draft.assert_not_called()
    draft_version_manager.create_next_version.assert_not_called()
    workflow_manager.fail_edit_to_preview.assert_not_called()
    workflow_manager.complete_edit.assert_called_once()
    _, kwargs = workflow_manager.complete_edit.call_args
    assert kwargs["generated_draft_reference"]["current_version"] == 2
    assert kwargs["pending_edit"]["status"] == "COMPLETED"


async def test_edit_draft_treats_a_different_operations_version_advance_as_genuinely_stale():
    # The pointer moved, but the new version's edit_instruction_reference
    # does NOT match this operation's operation_id -- a real conflict,
    # not a crash-recovery case.
    workflow = _make_workflow(pending_edit=_make_pending_edit(operation_id="op-1", expected_parent_version=1))
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(
        draft_version_manager,
        document=_make_current_document(
            version_number=2, parent_version_number=1,
            edit_instruction_reference={"instruction_id": "op-DIFFERENT", "created_at": "x"},
        ),
        pointer=_make_current_pointer(current_version_number=2),
    )
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(StaleDraftVersionError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()
    workflow_manager.complete_edit.assert_not_called()
    workflow_manager.fail_edit_to_preview.assert_called_once_with("wf-1")


async def test_edit_draft_raises_when_no_instruction_turn_found():
    workflow = _make_workflow(conversation=[], pending_edit=_make_pending_edit(instruction_turn_id="missing"))
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(NoPendingEditInstructionError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()


# --- unsupported platform switch (pre-flight) -----------------------------


async def test_edit_draft_rejects_unsupported_platform_switch_without_calling_claude():
    workflow = _make_workflow(
        conversation=[{"role": "user", "content": "Turn this into a LinkedIn post.", "turn_id": "op-1"}]
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()

    service = _make_service(claude_client=claude_client, draft_version_manager=draft_version_manager, workflow_manager=workflow_manager)

    with pytest.raises(UnsupportedEditRequestError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()
    workflow_manager.fail_edit_to_preview.assert_called_once_with("wf-1")


# --- corrupting failures --------------------------------------------------


async def test_edit_draft_raises_when_content_plan_missing():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(MissingContentPlanForGenerationError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    claude_client.edit_draft.assert_not_called()


async def test_edit_draft_raises_when_analysis_missing():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = None
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(MissingAnalysisForPlanningError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)


async def test_edit_draft_marks_permanent_failure_on_authentication_error():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()
    claude_client.edit_draft.side_effect = ClaudeAuthenticationError("bad key")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeAuthenticationError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)
    draft_version_manager.create_next_version.assert_not_called()


# --- retryable failures ---------------------------------------------------


async def test_edit_draft_sets_pending_retry_on_claude_retryable_error():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()
    claude_client.edit_draft.side_effect = ClaudeTimeoutError("timed out")

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(ClaudeTimeoutError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.set_edit_pending_retry.assert_called_once_with("wf-1")
    workflow_manager.update_state.assert_not_called()
    workflow_manager.fail_edit_to_preview.assert_not_called()
    draft_version_manager.create_next_version.assert_not_called()


async def test_edit_draft_sets_pending_retry_on_version_creation_conflict():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    draft_version_manager.create_next_version.side_effect = DraftVersionConflictError("orphaned")
    claude_client = MagicMock()
    claude_client.edit_draft.return_value = _claude_response(GOOD_REVISION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(DraftVersionConflictError):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.set_edit_pending_retry.assert_called_once_with("wf-1")
    workflow_manager.complete_edit.assert_not_called()


# --- non-corrupting: validation failure / corrective attempt -------------


async def test_edit_draft_regenerates_once_after_a_validation_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    draft_version_manager.create_next_version.return_value = _make_current_document(version_number=2)
    claude_client = MagicMock()
    claude_client.edit_draft.side_effect = [
        _claude_response(GENERIC_REVISION_PAYLOAD), _claude_response(GOOD_REVISION_PAYLOAD),
    ]

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    outcome = await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.edit_draft.call_count == 2
    assert outcome.version_number == 2


async def test_edit_draft_fails_to_preview_when_regeneration_also_invalid():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    claude_client = MagicMock()
    claude_client.edit_draft.side_effect = [
        _claude_response(GENERIC_REVISION_PAYLOAD), _claude_response(GENERIC_REVISION_PAYLOAD),
    ]

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    with pytest.raises(Exception):
        await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.edit_draft.call_count == 2
    workflow_manager.fail_edit_to_preview.assert_called_once_with("wf-1")
    workflow_manager.update_state.assert_not_called()  # non-corrupting -- never FAILED
    draft_version_manager.create_next_version.assert_not_called()


# --- never selects a secondary output --------------------------------------


async def test_edit_draft_never_touches_a_secondary_output():
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
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    draft_version_manager.create_next_version.return_value = _make_current_document(version_number=2)
    claude_client = MagicMock()
    claude_client.edit_draft.return_value = _claude_response(GOOD_REVISION_PAYLOAD)

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, content_plan_manager=content_plan_manager,
        draft_version_manager=draft_version_manager, workflow_manager=workflow_manager,
    )

    await service.edit_draft(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = draft_version_manager.create_next_version.call_args
    assert kwargs["output_id"] == "out-1"
    assert kwargs["output_type"] == "instagram_reel_caption"
