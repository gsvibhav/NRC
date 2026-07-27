from src.ai.clarification_models import ClarificationContext, ContextField, ContextSource
from src.ai.content_plan_prompts import (
    CONTENT_PLANNING_POLICY_VERSION,
    CONTENT_PLANNING_SYSTEM_PROMPT,
    CONTENT_PLAN_RESPONSE_SCHEMA,
    build_content_planning_user_prompt,
)
from src.ai.models import AnalysisResult

NOW = "2026-01-01T00:00:00+00:00"


def _make_analysis(**overrides):
    defaults = dict(
        summary="A founder speaking to camera.",
        visible_subjects=["a person"],
        visual_style=["natural light"],
        dominant_themes=["introduction"],
        brand_signals=["minimal set"],
        content_opportunities=["founder story"],
        quality_observations=[],
        safety_notes=[],
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def test_policy_version_is_a_positive_int():
    assert isinstance(CONTENT_PLANNING_POLICY_VERSION, int)
    assert CONTENT_PLANNING_POLICY_VERSION > 0


def test_system_prompt_is_strategist_not_copywriter():
    lowered = CONTENT_PLANNING_SYSTEM_PROMPT.lower()
    assert "strategist" in lowered
    assert "never write" in lowered or "never a copywriter" in lowered


def test_system_prompt_forbids_defaulting_to_every_channel():
    lowered = CONTENT_PLANNING_SYSTEM_PROMPT.lower()
    assert "do not propose an output just because a channel exists" in lowered


def test_system_prompt_requires_exactly_one_primary():
    assert "priority 1" in CONTENT_PLANNING_SYSTEM_PROMPT


def test_system_prompt_forbids_inventing_facts():
    assert "never invent" in CONTENT_PLANNING_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_hidden_reasoning():
    assert "hidden reasoning" in CONTENT_PLANNING_SYSTEM_PROMPT.lower()


def test_response_schema_rejects_additional_properties():
    assert CONTENT_PLAN_RESPONSE_SCHEMA["additionalProperties"] is False
    assert CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["strategy"]["additionalProperties"] is False
    assert CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["outputs"]["items"]["additionalProperties"] is False


def test_response_schema_required_matches_properties():
    assert set(CONTENT_PLAN_RESPONSE_SCHEMA["required"]) == set(CONTENT_PLAN_RESPONSE_SCHEMA["properties"].keys())


def test_strategy_schema_required_matches_its_properties():
    strategy_schema = CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["strategy"]
    assert set(strategy_schema["required"]) == set(strategy_schema["properties"].keys())


def test_output_item_schema_required_matches_its_properties():
    output_schema = CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["outputs"]["items"]
    assert set(output_schema["required"]) == set(output_schema["properties"].keys())


# --- build_content_planning_user_prompt -----------------------------------


def test_build_prompt_includes_analysis_fields():
    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[], max_outputs=3,
    )

    assert "A founder speaking to camera." in prompt
    assert "founder story" in prompt


def test_build_prompt_handles_missing_analysis():
    prompt = build_content_planning_user_prompt(
        analysis=None, clarification_context=None, conversation_turns=[], max_outputs=3,
    )

    assert "not available" in prompt


def test_build_prompt_includes_clarification_context_values():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=context, conversation_turns=[], max_outputs=3,
    )

    assert "NRC" in prompt


def test_build_prompt_notes_when_no_clarification_was_needed():
    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[], max_outputs=3,
    )

    assert "no clarification was needed" in prompt.lower()


def test_build_prompt_includes_output_registry():
    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[], max_outputs=3,
    )

    assert "instagram_reel_caption" in prompt
    assert "linkedin_post" in prompt


def test_build_prompt_includes_max_outputs():
    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[], max_outputs=3,
    )

    assert "3" in prompt


def test_build_prompt_bounds_conversation_history():
    turns = [{"role": "user", "content": f"turn {i}"} for i in range(20)]

    prompt = build_content_planning_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=turns, max_outputs=3,
    )

    assert "turn 19" in prompt
    assert "turn 0" not in prompt
