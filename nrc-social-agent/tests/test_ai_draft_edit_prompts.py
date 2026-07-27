from src.ai.clarification_models import ClarificationContext, ContextField, ContextSource
from src.ai.content_plan_models import OutputType, PlanStrategy, PlannedOutput
from src.ai.draft_edit_prompts import (
    DRAFT_EDIT_RESPONSE_SCHEMAS,
    DRAFT_EDIT_SYSTEM_PROMPTS,
    EDIT_PROMPT_VERSION,
    INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT,
    build_draft_edit_user_prompt,
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


CURRENT_CONTENT = {"caption": "The founder speaks to camera about NRC.", "hashtags": ["nrc"], "cta": "Explore NRC."}


def test_prompt_version_is_a_positive_int():
    assert isinstance(EDIT_PROMPT_VERSION, int)
    assert EDIT_PROMPT_VERSION > 0


def test_only_instagram_reel_caption_is_registered():
    assert set(DRAFT_EDIT_SYSTEM_PROMPTS.keys()) == {"instagram_reel_caption"}
    assert set(DRAFT_EDIT_RESPONSE_SCHEMAS.keys()) == {"instagram_reel_caption"}


def test_response_schema_reused_unchanged_from_generation():
    from src.ai.draft_prompts import DRAFT_RESPONSE_SCHEMAS

    assert DRAFT_EDIT_RESPONSE_SCHEMAS is DRAFT_RESPONSE_SCHEMAS


def test_system_prompt_declares_output_type_and_platform_authoritative():
    lowered = INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()
    assert "output type and target platform are authoritative" in lowered
    assert "must not change" in lowered


def test_system_prompt_forbids_variants_and_alternates():
    lowered = INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()
    assert "no variants" in lowered
    assert "option a" in lowered


def test_system_prompt_addresses_platform_switch_requests():
    lowered = INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()
    assert "linkedin post" in lowered
    assert "do not attempt it" in lowered


def test_system_prompt_forbids_inventing_facts():
    assert "never invent" in INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()


def test_system_prompt_forbids_hidden_reasoning_and_internal_terms():
    lowered = INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()
    assert "hidden reasoning" in lowered
    assert "edit instruction" in lowered  # named as a banned term to leak


def test_system_prompt_instructs_targeted_vs_broad_rewrite_behavior():
    lowered = INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT.lower()
    assert "preserve everything about the current draft" in lowered
    assert "broad rewrite only when" in lowered


def test_response_schema_rejects_additional_properties():
    schema = DRAFT_EDIT_RESPONSE_SCHEMAS["instagram_reel_caption"]
    assert schema["additionalProperties"] is False


def test_response_schema_required_matches_properties():
    schema = DRAFT_EDIT_RESPONSE_SCHEMAS["instagram_reel_caption"]
    assert set(schema["required"]) == set(schema["properties"].keys())


# --- build_draft_edit_user_prompt -----------------------------------------


def test_build_prompt_includes_current_content():
    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Make it shorter.",
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=None, current_version_number=1,
    )

    assert "The founder speaks to camera about NRC." in prompt
    assert "nrc" in prompt
    assert "Explore NRC." in prompt


def test_build_prompt_includes_the_instruction():
    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Remove the hashtags.",
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=None, current_version_number=1,
    )

    assert "Remove the hashtags." in prompt


def test_build_prompt_includes_current_version_number():
    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Make it shorter.",
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=None, current_version_number=3,
    )

    assert "version 3" in prompt.lower() or "Version 3" in prompt


def test_build_prompt_includes_strategy_and_output():
    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Make it shorter.",
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=None, current_version_number=1,
    )

    assert "build founder credibility" in prompt
    assert "instagram_reel_caption" in prompt


def test_build_prompt_handles_missing_analysis():
    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Make it shorter.",
        strategy=_make_strategy(), output=_make_output(), analysis=None,
        clarification_context=None, current_version_number=1,
    )

    assert "not available" in prompt


def test_build_prompt_includes_clarification_context_values():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction="Make it shorter.",
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=context, current_version_number=1,
    )

    assert "NRC" in prompt


def test_build_prompt_truncates_a_very_long_instruction():
    long_instruction = "please make it shorter " * 100

    prompt = build_draft_edit_user_prompt(
        current_content=CURRENT_CONTENT, instruction=long_instruction,
        strategy=_make_strategy(), output=_make_output(), analysis=_make_analysis(),
        clarification_context=None, current_version_number=1,
    )

    assert long_instruction not in prompt
    assert "…" in prompt
