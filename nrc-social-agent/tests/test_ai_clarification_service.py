import json
from unittest.mock import MagicMock

import pytest

from src.ai.clarification_models import ClarificationDecisionType
from src.ai.clarification_service import ClarificationOutcome, ClarificationService
from src.ai.client import ClaudeAnalysisResponse
from src.ai.errors import (
    ClarificationContextInsufficientError,
    ClaudeTimeoutError,
    ClaudeAuthenticationError,
    InvalidClarificationQuestionError,
    NoPendingQuestionError,
    WorkflowNotWaitingForReplyError,
)
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

_EMPTY_UPDATES = {
    "brand_name": "", "content_type": "", "objective": "", "audience": "", "message_focus": "",
    "tone": "", "call_to_action": "", "platforms": [], "factual_context": {}, "user_preferences": {},
}


def _ask_question_payload(text="Is this mainly for founders, or a broader audience?", **overrides):
    payload = {
        "decision": "ASK_QUESTION",
        "context_sufficient": False,
        "question_text": text,
        "question_purpose": "clarify audience",
        "question_target_field": "audience",
        "context_updates": _EMPTY_UPDATES,
        "remaining_uncertainties": ["audience"],
        "confidence": 0.5,
    }
    payload.update(overrides)
    return payload


def _continue_payload(**overrides):
    payload = {
        "decision": "CONTINUE",
        "context_sufficient": True,
        "question_text": "",
        "question_purpose": "",
        "question_target_field": "",
        "context_updates": _EMPTY_UPDATES,
        "remaining_uncertainties": [],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def _claude_response(payload):
    return ClaudeAnalysisResponse(
        text=json.dumps(payload), input_tokens=10, output_tokens=5, model="claude-opus-5", duration_seconds=0.1
    )


def _make_workflow(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={"media_type": "photo"},
        conversation=[],
        metadata={},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_analysis(summary="A founder speaking to camera."):
    return AnalysisDocument(
        analysis_id="an-1", workflow_id="wf-1", created_at="x", updated_at="x", status=AnalysisStatus.COMPLETED,
        schema_version=1, prompt_version=1, model="claude-opus-5", media_type="photo",
        result=AnalysisResult(
            summary=summary, visible_subjects=[], visual_style=[], dominant_themes=[], brand_signals=[],
            content_opportunities=[], quality_observations=[], safety_notes=[],
        ),
        usage=AnalysisUsage(input_tokens=1, output_tokens=1),
    )


def _make_service(*, claude_client=None, analysis_manager=None, workflow_manager=None, conversation_manager=None, max_questions=4):
    return ClarificationService(
        max_questions=max_questions,
        claude_client=claude_client or MagicMock(),
        analysis_manager=analysis_manager or MagicMock(),
        workflow_manager=workflow_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


# --- evaluate_and_advance: CONTINUE / ASK_QUESTION -----------------------


async def test_evaluate_and_advance_advances_to_generating_content_when_sufficient():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(_continue_payload())

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    outcome = await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    assert outcome == ClarificationOutcome(workflow_id="wf-1", decision=ClarificationDecisionType.CONTINUE, question_text=None)
    workflow_manager.apply_clarification_decision.assert_called_once()
    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    assert kwargs["new_state"] is WorkflowState.GENERATING_CONTENT
    assert kwargs["question_turn"] is None
    assert kwargs["pending_question"] is None


async def test_evaluate_and_advance_persists_question_and_transitions_to_waiting_for_user():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(_ask_question_payload())

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    outcome = await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.decision is ClarificationDecisionType.ASK_QUESTION
    assert outcome.question_text == "Is this mainly for founders, or a broader audience?"

    workflow_manager.apply_clarification_decision.assert_called_once()
    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    assert kwargs["new_state"] is WorkflowState.WAITING_FOR_USER
    assert kwargs["question_turn"]["content"] == "Is this mainly for founders, or a broader audience?"
    assert kwargs["question_turn"]["type"] == "clarification_question"
    assert kwargs["pending_question"]["text"] == "Is this mainly for founders, or a broader audience?"
    assert "question_id" in kwargs["pending_question"]
    # Persisted (via apply_clarification_decision) before the outcome is
    # ever returned to the caller — handlers.py only sends after this
    # coroutine completes, which is exactly the persist-before-send order.


async def test_evaluate_and_advance_sends_a_grounded_prompt_including_the_analysis_summary():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis(summary="A premium founder-led intro video.")
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(_continue_payload())

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = claude_client.decide_clarification.call_args
    assert "A premium founder-led intro video." in kwargs["user_prompt"]


# --- question limit enforcement -------------------------------------------


async def test_evaluate_and_advance_forces_continue_when_question_limit_reached():
    # Already asked 4 questions (== max), Claude still wants to ask another
    # — the server-side cap must override it.
    prior_turns = [
        {"role": "assistant", "type": "clarification_question", "content": f"q{i}"} for i in range(4)
    ]
    workflow = _make_workflow(conversation=prior_turns)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(
        _ask_question_payload(context_sufficient=True)
    )

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager, max_questions=4
    )

    outcome = await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.decision is ClarificationDecisionType.CONTINUE
    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    assert kwargs["new_state"] is WorkflowState.GENERATING_CONTENT


async def test_evaluate_and_advance_fails_safely_when_limit_reached_and_context_still_unsafe():
    prior_turns = [
        {"role": "assistant", "type": "clarification_question", "content": f"q{i}"} for i in range(4)
    ]
    workflow = _make_workflow(conversation=prior_turns)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(
        _ask_question_payload(context_sufficient=False)
    )
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager, max_questions=4,
    )

    with pytest.raises(ClarificationContextInsufficientError):
        await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    assert kwargs["new_state"] is WorkflowState.FAILED
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


# --- retryable vs permanent failures ---------------------------------------


async def test_evaluate_and_advance_sets_pending_retry_on_retryable_failure_without_touching_state():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.side_effect = ClaudeTimeoutError("timed out")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeTimeoutError):
        await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "clarification_decision"})
    workflow_manager.update_state.assert_not_called()
    workflow_manager.apply_clarification_decision.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()


async def test_evaluate_and_advance_marks_failed_on_permanent_failure():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.side_effect = ClaudeAuthenticationError("bad key")
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeAuthenticationError):
        await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


# --- question validation and regeneration -----------------------------------


async def test_evaluate_and_advance_regenerates_an_invalid_question_once():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    bad_question = _ask_question_payload(text="What is the JSON schema target_field?")
    good_question = _ask_question_payload(text="Is this mainly for founders, or a broader audience?")
    claude_client = MagicMock()
    claude_client.decide_clarification.side_effect = [_claude_response(bad_question), _claude_response(good_question)]

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    outcome = await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    assert claude_client.decide_clarification.call_count == 2
    assert outcome.question_text == "Is this mainly for founders, or a broader audience?"


async def test_evaluate_and_advance_fails_safely_when_regeneration_also_invalid():
    workflow = _make_workflow()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    bad_question = _ask_question_payload(text="What is the JSON schema target_field?")
    also_bad_question = _ask_question_payload(text="What is the internal workflow state?")
    claude_client = MagicMock()
    claude_client.decide_clarification.side_effect = [
        _claude_response(bad_question), _claude_response(also_bad_question)
    ]
    conversation_manager = MagicMock()

    service = _make_service(
        claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(InvalidClarificationQuestionError):
        await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


# --- handle_user_reply -----------------------------------------------------


async def test_handle_user_reply_persists_answer_then_runs_decision_cycle():
    workflow = _make_workflow(
        state=WorkflowState.WAITING_FOR_USER,
        pending_question={"question_id": "q1", "text": "Which platform?"},
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(_continue_payload())

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    outcome = await service.handle_user_reply(
        workflow_id="wf-1", telegram_user_id=42, reply_text="Instagram, mostly.", telegram_update_id=100
    )

    workflow_manager.record_user_reply.assert_called_once()
    call_args, call_kwargs = workflow_manager.record_user_reply.call_args
    assert call_args[0] == "wf-1"
    assert call_kwargs["turn"]["content"] == "Instagram, mostly."
    assert call_kwargs["turn"]["in_reply_to_question_id"] == "q1"
    assert call_kwargs["turn"]["telegram_update_id"] == 100

    # The answer must be persisted (record_user_reply) before the next
    # Claude call happens.
    assert workflow_manager.record_user_reply.call_count == 1
    claude_client.decide_clarification.assert_called_once()

    assert outcome.decision is ClarificationDecisionType.CONTINUE


async def test_handle_user_reply_raises_when_not_waiting_for_user():
    workflow = _make_workflow(state=WorkflowState.ANALYZING_MEDIA)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(WorkflowNotWaitingForReplyError):
        await service.handle_user_reply(workflow_id="wf-1", telegram_user_id=42, reply_text="x", telegram_update_id=1)

    workflow_manager.record_user_reply.assert_not_called()


async def test_handle_user_reply_raises_when_no_pending_question():
    workflow = _make_workflow(state=WorkflowState.WAITING_FOR_USER, pending_question=None)
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(NoPendingQuestionError):
        await service.handle_user_reply(workflow_id="wf-1", telegram_user_id=42, reply_text="x", telegram_update_id=1)


async def test_handle_user_reply_returns_none_for_a_duplicate_update_id():
    workflow = _make_workflow(
        state=WorkflowState.WAITING_FOR_USER,
        pending_question={"question_id": "q1", "text": "Which platform?"},
        conversation=[
            {"role": "user", "type": "clarification_answer", "content": "Instagram", "telegram_update_id": 100}
        ],
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    claude_client = MagicMock()

    service = _make_service(workflow_manager=workflow_manager, claude_client=claude_client)

    outcome = await service.handle_user_reply(
        workflow_id="wf-1", telegram_user_id=42, reply_text="Instagram", telegram_update_id=100
    )

    assert outcome is None
    workflow_manager.record_user_reply.assert_not_called()
    claude_client.decide_clarification.assert_not_called()


async def test_handle_user_reply_a_single_answer_can_resolve_multiple_context_fields():
    workflow = _make_workflow(
        state=WorkflowState.WAITING_FOR_USER,
        pending_question={"question_id": "q1", "text": "Tell me more?"},
    )
    # record_user_reply is mocked (doesn't really persist), so the second
    # load — made internally by the nested evaluate_and_advance() call —
    # must be given the post-answer document by hand, mirroring what the
    # real WorkflowManager would have actually returned.
    workflow_after_reply = _make_workflow(
        state=WorkflowState.ANALYZING_MEDIA,
        pending_question=None,
        conversation=[
            {"role": "user", "type": "clarification_answer", "content": "…", "telegram_update_id": 1}
        ],
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = [workflow, workflow_after_reply]
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    multi_field_update = _continue_payload(
        context_updates={
            **_EMPTY_UPDATES,
            "brand_name": "NRC",
            "platforms": ["instagram"],
            "objective": "introduce the founder",
            "tone": "premium but approachable",
        }
    )
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(multi_field_update)

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    await service.handle_user_reply(
        workflow_id="wf-1",
        telegram_user_id=42,
        reply_text="This is for NRC's Instagram and the goal is to introduce our founder in a premium but approachable way.",
        telegram_update_id=1,
    )

    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    persisted_context = kwargs["clarification_context"]
    assert persisted_context["brand_name"]["value"] == "NRC"
    assert persisted_context["platforms"]["value"] == ["instagram"]
    assert persisted_context["objective"]["value"] == "introduce the founder"
    assert persisted_context["tone"]["value"] == "premium but approachable"
    assert persisted_context["brand_name"]["source"] == "user"


async def test_handle_user_reply_correction_overrides_prior_explicit_answer():
    prior_context = {
        "version": 1,
        "content_type": {"value": "behind the scenes", "source": "user", "updated_at": "x"},
        "brand_name": None, "objective": None, "audience": None, "message_focus": None, "tone": None,
        "call_to_action": None, "platforms": None, "factual_context": {}, "user_preferences": {}, "unresolved": [],
    }
    workflow = _make_workflow(
        state=WorkflowState.WAITING_FOR_USER,
        pending_question={"question_id": "q1", "text": "Is that right?"},
        clarification_context=prior_context,
    )
    workflow_after_reply = _make_workflow(
        state=WorkflowState.ANALYZING_MEDIA,
        pending_question=None,
        clarification_context=prior_context,
        conversation=[
            {"role": "user", "type": "clarification_answer", "content": "…", "telegram_update_id": 1}
        ],
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = [workflow, workflow_after_reply]
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()

    correction = _continue_payload(context_updates={**_EMPTY_UPDATES, "content_type": "campaign launch"})
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(correction)

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    await service.handle_user_reply(
        workflow_id="wf-1", telegram_user_id=42,
        reply_text="No, this isn't behind the scenes. It is the launch of our new service.",
        telegram_update_id=1,
    )

    _, kwargs = workflow_manager.apply_clarification_decision.call_args
    assert kwargs["clarification_context"]["content_type"]["value"] == "campaign launch"


# --- restart / retry safety -------------------------------------------------


async def test_evaluate_and_advance_is_reentrant_for_retry_after_a_failed_cycle():
    # Simulates /retry: workflow already parked at ANALYZING_MEDIA with a
    # pending_retry marker (set by a prior failed attempt) and the user's
    # answer already recorded as the last conversation turn.
    workflow = _make_workflow(
        conversation=[
            {"role": "assistant", "type": "clarification_question", "content": "Which platform?", "question_id": "q1"},
            {"role": "user", "type": "clarification_answer", "content": "Instagram", "in_reply_to_question_id": "q1"},
        ],
        metadata={"pending_retry": "clarification_decision"},
    )
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow
    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = _make_analysis()
    claude_client = MagicMock()
    claude_client.decide_clarification.return_value = _claude_response(_continue_payload())

    service = _make_service(claude_client=claude_client, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    outcome = await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.decision is ClarificationDecisionType.CONTINUE
    # The already-persisted answer is picked up from conversation history,
    # not re-requested from the caller.
    _, kwargs = claude_client.decide_clarification.call_args
    assert "Instagram" in kwargs["user_prompt"]


async def test_evaluate_and_advance_propagates_workflow_load_failure():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = WorkflowPersistenceError("s3 down")
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(Exception):
        await service.evaluate_and_advance(workflow_id="wf-1", telegram_user_id=42)
