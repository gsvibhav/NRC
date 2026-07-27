from src.ai.clarification_models import ClarificationContext, ContextField, ContextSource
from src.ai.content_plan_models import OutputType, PlanStrategy, PlannedOutput
from src.ai.draft_prompts import (
    DRAFT_GENERATION_SYSTEM_PROMPTS,
    DRAFT_PROMPT_VERSION,
    DRAFT_RESPONSE_SCHEMAS,
    INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA,
    INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT,
    build_draft_generation_user_prompt,
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


def _make_strategy(**overrides):
    defaults = dict(
        content_type="founder_introduction",
        primary_objective="build founder credibility",
        central_message="NRC connects branding, content and advertising into one system",
        brand_positioning="premium, connected",
        cta_direction="invite viewers to explore NRC",
    )
    defaults.update(overrides)
    return PlanStrategy(**defaults)


def _make_output(**overrides):
    defaults = dict(
        output_id="out-1",
        output_type=OutputType.INSTAGRAM_REEL_CAPTION.value,
        priority=1,
        purpose="build founder credibility",
        message_focus="make the idea immediate and memorable",
        cta_direction="invite viewers to explore NRC without a hard sell",
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


# --- static prompt/schema content -----------------------------------------


def test_prompt_version_is_a_positive_int():
    assert isinstance(DRAFT_PROMPT_VERSION, int)
    assert DRAFT_PROMPT_VERSION > 0


def test_only_instagram_reel_caption_is_registered():
    assert set(DRAFT_GENERATION_SYSTEM_PROMPTS.keys()) == {"instagram_reel_caption"}
    assert set(DRAFT_RESPONSE_SCHEMAS.keys()) == {"instagram_reel_caption"}


def test_system_prompt_declares_planning_decision_authoritative():
    lowered = INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()
    assert "planning decision is authoritative" in lowered
    assert "do not reconsider which output" in lowered


def test_system_prompt_forbids_variants_and_alternates():
    lowered = INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()
    assert "no variants" in lowered
    assert "option a" in lowered or "option a / option b" in lowered


def test_system_prompt_forbids_generic_cta_phrases():
    lowered = INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()
    for phrase in ("dm us now", "book a call today", "click the link in bio"):
        assert phrase in lowered


def test_system_prompt_forbids_inventing_facts():
    assert "never invent" in INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_hidden_reasoning_and_internal_terms():
    lowered = INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()
    assert "hidden reasoning" in lowered
    assert "content plan" in lowered  # named as a banned term to leak
    assert "anthropic" in lowered


def test_system_prompt_forbids_fixed_structure():
    lowered = INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT.lower()
    assert "no fixed skeleton" in lowered or "no mandatory" in lowered


def test_response_schema_rejects_additional_properties():
    assert INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA["additionalProperties"] is False


def test_response_schema_required_matches_properties():
    assert set(INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA["required"]) == set(
        INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA["properties"].keys()
    )


def test_response_schema_has_no_variant_or_option_fields():
    properties = INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA["properties"].keys()
    assert properties == {"caption", "hashtags", "cta"}


# --- build_draft_generation_user_prompt -----------------------------------


def test_build_prompt_includes_analysis_fields():
    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "A founder speaking to camera." in prompt
    assert "introduction" in prompt  # dominant_themes


def test_build_prompt_handles_missing_analysis():
    prompt = build_draft_generation_user_prompt(
        analysis=None, clarification_context=None, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "not available" in prompt


def test_build_prompt_includes_clarification_context_values():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=context, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "NRC" in prompt


def test_build_prompt_notes_when_no_clarification_was_needed():
    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "no clarification was needed" in prompt.lower()


def test_build_prompt_includes_strategy_fields():
    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "build founder credibility" in prompt
    assert "NRC connects branding, content and advertising into one system" in prompt


def test_build_prompt_includes_only_the_selected_output_not_other_types():
    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=[],
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "instagram_reel_caption" in prompt
    # No secondary/excluded output type should ever be mentioned — this
    # builder takes exactly one `output` parameter, so there's no way for
    # a second output type's identifier to appear unless it were literally
    # embedded in a field value (not the case for the default fixture).
    assert "linkedin_post" not in prompt
    assert "threads_post" not in prompt


def test_build_prompt_bounds_conversation_history():
    turns = [{"role": "user", "content": f"turn {i}"} for i in range(20)]

    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=turns,
        strategy=_make_strategy(), output=_make_output(),
    )

    assert "turn 19" in prompt
    assert "turn 0" not in prompt


def test_build_prompt_truncates_very_long_turn_text():
    long_text = "x" * 1000
    turns = [{"role": "user", "content": long_text}]

    prompt = build_draft_generation_user_prompt(
        analysis=_make_analysis(), clarification_context=None, conversation_turns=turns,
        strategy=_make_strategy(), output=_make_output(),
    )

    assert long_text not in prompt
    assert "…" in prompt
