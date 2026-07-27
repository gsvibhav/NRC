from src.ai.clarification_models import ClarificationContext, ContextField, ContextSource
from src.ai.clarification_prompts import (
    CLARIFICATION_POLICY_VERSION,
    CLARIFICATION_RESPONSE_SCHEMA,
    CLARIFICATION_SYSTEM_PROMPT,
    MAX_CONVERSATION_TURNS_IN_PROMPT,
    build_clarification_user_prompt,
)

NOW = "2026-01-01T00:00:00+00:00"


def test_policy_version_is_a_positive_int():
    assert isinstance(CLARIFICATION_POLICY_VERSION, int)
    assert CLARIFICATION_POLICY_VERSION > 0


def test_system_prompt_forbids_a_fixed_questionnaire():
    lowered = CLARIFICATION_SYSTEM_PROMPT.lower()
    assert "not a form" in lowered or "never walk through a fixed checklist" in lowered


def test_system_prompt_requires_one_question_at_a_time():
    assert "at most one thing per turn" in CLARIFICATION_SYSTEM_PROMPT


def test_system_prompt_forbids_inventing_facts():
    assert "never invent" in CLARIFICATION_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_identifying_real_people():
    assert "real people" in CLARIFICATION_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_hidden_reasoning():
    assert "hidden reasoning" in CLARIFICATION_SYSTEM_PROMPT.lower()


def test_response_schema_rejects_additional_properties():
    assert CLARIFICATION_RESPONSE_SCHEMA["additionalProperties"] is False
    assert CLARIFICATION_RESPONSE_SCHEMA["properties"]["context_updates"]["additionalProperties"] is False


def test_response_schema_required_matches_properties():
    assert set(CLARIFICATION_RESPONSE_SCHEMA["required"]) == set(CLARIFICATION_RESPONSE_SCHEMA["properties"].keys())


def test_context_updates_schema_required_matches_its_properties():
    context_updates_schema = CLARIFICATION_RESPONSE_SCHEMA["properties"]["context_updates"]
    assert set(context_updates_schema["required"]) == set(context_updates_schema["properties"].keys())


# --- build_clarification_user_prompt --------------------------------------


def test_build_prompt_includes_analysis_summary():
    prompt = build_clarification_user_prompt(
        analysis_summary="A founder speaking to camera.",
        context=ClarificationContext(),
        conversation_turns=[],
        pending_question=None,
        question_count=0,
        max_questions=4,
    )

    assert "A founder speaking to camera." in prompt


def test_build_prompt_handles_missing_analysis_summary():
    prompt = build_clarification_user_prompt(
        analysis_summary=None, context=ClarificationContext(), conversation_turns=[],
        pending_question=None, question_count=0, max_questions=4,
    )

    assert "not available" in prompt


def test_build_prompt_includes_known_context_values():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=context, conversation_turns=[],
        pending_question=None, question_count=0, max_questions=4,
    )

    assert "NRC" in prompt


def test_build_prompt_bounds_conversation_history_to_the_most_recent_turns():
    turns = [{"role": "assistant", "content": f"turn {i}"} for i in range(20)]

    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=turns,
        pending_question=None, question_count=0, max_questions=4,
    )

    assert "turn 19" in prompt  # most recent kept
    assert "turn 0" not in prompt  # oldest dropped
    lines = prompt.splitlines()
    included = sum(1 for i in range(20) if f"- assistant: turn {i}" in lines)
    assert included == MAX_CONVERSATION_TURNS_IN_PROMPT


def test_build_prompt_includes_pending_question_when_present():
    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=[],
        pending_question={"question_id": "q1", "text": "Which platform?"},
        question_count=1, max_questions=4,
    )

    assert "Which platform?" in prompt


def test_build_prompt_includes_latest_user_answer_derived_from_conversation_turns():
    turns = [
        {"role": "assistant", "type": "clarification_question", "content": "Which platform?"},
        {"role": "user", "type": "clarification_answer", "content": "Instagram, mostly."},
    ]

    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=turns,
        pending_question={"question_id": "q1", "text": "Which platform?"}, question_count=1, max_questions=4,
    )

    assert "Instagram, mostly." in prompt
    assert "Latest user reply to interpret" in prompt


def test_build_prompt_omits_latest_answer_marker_when_last_turn_is_not_from_user():
    turns = [{"role": "assistant", "type": "clarification_question", "content": "Which platform?"}]

    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=turns,
        pending_question={"question_id": "q1", "text": "Which platform?"}, question_count=1, max_questions=4,
    )

    assert "Latest user reply to interpret" not in prompt


def test_build_prompt_signals_when_the_question_limit_is_reached():
    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=[],
        pending_question=None, question_count=4, max_questions=4,
    )

    assert "must return CONTINUE" in prompt


def test_build_prompt_does_not_signal_limit_when_under_it():
    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=[],
        pending_question=None, question_count=1, max_questions=4,
    )

    assert "must return CONTINUE" not in prompt


def test_build_prompt_truncates_an_unusually_long_turn():
    long_text = "x" * 5000
    turns = [{"role": "user", "content": long_text}]

    prompt = build_clarification_user_prompt(
        analysis_summary="x", context=ClarificationContext(), conversation_turns=turns,
        pending_question=None, question_count=0, max_questions=4,
    )

    assert len(prompt) < 5000
