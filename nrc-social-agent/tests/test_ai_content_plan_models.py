import pytest

from src.ai.content_plan_models import (
    OUTPUT_TYPE_REGISTRY,
    ContentPlanDocument,
    ContentPlanStatus,
    ContentPlanUsage,
    ExcludedOutput,
    OutputType,
    PlanStrategy,
    PlannedOutput,
)
from src.ai.errors import (
    ContentPlanSchemaMismatchError,
    ContentPlanVersionMismatchError,
    InvalidContentPlanStatusError,
)


def _make_strategy(**overrides):
    defaults = dict(
        content_type="founder_introduction",
        primary_objective="build founder credibility",
        central_message="NRC connects branding, content and advertising into one system",
        brand_positioning="premium, connected",
        cta_direction="invite viewers to explore NRC",
        audience=["business owners"],
        tone_direction=["confident", "premium"],
        factual_constraints=["brand: NRC"],
        avoid=["performance claims"],
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


def _make_document(**overrides):
    defaults = dict(
        plan_id="plan-1",
        workflow_id="wf-1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=ContentPlanStatus.COMPLETED,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        strategy=_make_strategy(),
        outputs=[_make_output()],
        excluded_outputs=[ExcludedOutput(output_type=OutputType.LINKEDIN_POST.value, reason="not enough detail")],
        usage=ContentPlanUsage(input_tokens=10, output_tokens=5),
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


# --- OUTPUT_TYPE_REGISTRY --------------------------------------------------


def test_registry_covers_every_output_type_enum_member():
    assert set(OUTPUT_TYPE_REGISTRY.keys()) == set(OutputType)


def test_registry_entries_do_not_claim_publishing_support():
    # No publishing adapter exists yet for any output type (ROADMAP Phase 3+).
    for definition in OUTPUT_TYPE_REGISTRY.values():
        assert definition.supports_planning is True
        assert definition.supports_publishing is False


def test_only_instagram_reel_caption_supports_generation():
    # As of Milestone 6, instagram_reel_caption is the sole
    # supports_generation=True entry (see draft_generation_service.py) —
    # every other registered output type remains planning-only until a
    # future milestone deliberately extends generation support to it.
    for output_type, definition in OUTPUT_TYPE_REGISTRY.items():
        if output_type is OutputType.INSTAGRAM_REEL_CAPTION:
            assert definition.supports_generation is True
        else:
            assert definition.supports_generation is False


# --- PlanStrategy -----------------------------------------------------


def test_plan_strategy_round_trips_through_dict():
    strategy = _make_strategy()

    assert PlanStrategy.from_dict(strategy.to_dict()) == strategy


def test_plan_strategy_from_dict_rejects_missing_required_field():
    data = _make_strategy().to_dict()
    del data["central_message"]

    with pytest.raises(ContentPlanSchemaMismatchError):
        PlanStrategy.from_dict(data)


def test_plan_strategy_from_dict_rejects_non_list_audience():
    data = _make_strategy().to_dict()
    data["audience"] = "not a list"

    with pytest.raises(ContentPlanSchemaMismatchError):
        PlanStrategy.from_dict(data)


def test_plan_strategy_optional_fields_default_to_none():
    data = _make_strategy().to_dict()

    strategy = PlanStrategy.from_dict(data)

    assert strategy.content_type_description is None
    assert strategy.supporting_objective is None


# --- PlannedOutput ------------------------------------------------------


def test_planned_output_round_trips_through_dict():
    output = _make_output()

    assert PlannedOutput.from_dict(output.to_dict()) == output


def test_planned_output_defaults_generation_status_not_started():
    data = _make_output().to_dict()
    del data["generation_status"]

    output = PlannedOutput.from_dict(data)

    assert output.generation_status == "NOT_STARTED"


def test_planned_output_from_dict_rejects_non_int_priority():
    data = _make_output().to_dict()
    data["priority"] = "1"

    with pytest.raises(ContentPlanSchemaMismatchError):
        PlannedOutput.from_dict(data)


def test_planned_output_from_dict_rejects_bool_priority():
    data = _make_output().to_dict()
    data["priority"] = True

    with pytest.raises(ContentPlanSchemaMismatchError):
        PlannedOutput.from_dict(data)


def test_planned_output_from_dict_rejects_empty_purpose():
    data = _make_output().to_dict()
    data["purpose"] = ""

    with pytest.raises(ContentPlanSchemaMismatchError):
        PlannedOutput.from_dict(data)


# --- ExcludedOutput -------------------------------------------------------


def test_excluded_output_round_trips_through_dict():
    excluded = ExcludedOutput(output_type=OutputType.LINKEDIN_POST.value, reason="not enough project detail")

    assert ExcludedOutput.from_dict(excluded.to_dict()) == excluded


def test_excluded_output_from_dict_rejects_missing_reason():
    with pytest.raises(ContentPlanSchemaMismatchError):
        ExcludedOutput.from_dict({"output_type": "linkedin_post"})


# --- ContentPlanUsage ---------------------------------------------------


def test_content_plan_usage_round_trips_through_dict():
    usage = ContentPlanUsage(input_tokens=10, output_tokens=5)

    assert ContentPlanUsage.from_dict(usage.to_dict()) == usage


def test_content_plan_usage_rejects_bool_tokens():
    with pytest.raises(ContentPlanSchemaMismatchError):
        ContentPlanUsage.from_dict({"input_tokens": True, "output_tokens": 5})


# --- ContentPlanDocument --------------------------------------------------


def test_content_plan_document_round_trips_through_dict():
    document = _make_document()

    assert ContentPlanDocument.from_dict(document.to_dict()) == document


def test_content_plan_document_round_trips_with_no_strategy_or_usage():
    document = _make_document(status=ContentPlanStatus.FAILED, strategy=None, outputs=[], usage=None)

    restored = ContentPlanDocument.from_dict(document.to_dict())

    assert restored.strategy is None
    assert restored.usage is None
    assert restored.status is ContentPlanStatus.FAILED


def test_content_plan_document_from_dict_rejects_unsupported_version():
    data = _make_document().to_dict()
    data["version"] = 999

    with pytest.raises(ContentPlanVersionMismatchError):
        ContentPlanDocument.from_dict(data)


def test_content_plan_document_from_dict_rejects_unrecognized_status():
    data = _make_document().to_dict()
    data["status"] = "NOT_A_STATUS"

    with pytest.raises(InvalidContentPlanStatusError):
        ContentPlanDocument.from_dict(data)


def test_content_plan_document_from_dict_rejects_duplicate_output_ids():
    data = _make_document(
        outputs=[_make_output(output_id="dup", output_type="instagram_reel_caption"), _make_output(output_id="dup", output_type="linkedin_post")]
    ).to_dict()

    with pytest.raises(ContentPlanSchemaMismatchError, match="duplicate"):
        ContentPlanDocument.from_dict(data)


def test_content_plan_document_from_dict_rejects_non_dict_input():
    with pytest.raises(ContentPlanSchemaMismatchError):
        ContentPlanDocument.from_dict(["not", "a", "dict"])


def test_content_plan_document_to_dict_never_includes_raw_reasoning_field():
    data = _make_document().to_dict()

    assert "reasoning" not in data
    assert "raw_response" not in data
    assert "chain_of_thought" not in data
