import json

import pytest

from src.ai.content_plan_parser import parse_content_plan_response
from src.ai.errors import ContentPlanSchemaMismatchError, MalformedContentPlanResponseError

VALID_STRATEGY = {
    "content_type": "founder_introduction",
    "content_type_description": "",
    "primary_objective": "build founder credibility",
    "supporting_objective": "",
    "audience": ["business owners"],
    "central_message": "NRC connects branding, content and advertising into one system",
    "brand_positioning": "premium, connected",
    "tone_direction": ["confident", "premium"],
    "cta_direction": "invite viewers to explore NRC",
    "factual_constraints": ["brand: NRC"],
    "avoid": ["performance claims"],
}

VALID_OUTPUT = {
    "output_type": "instagram_reel_caption",
    "priority": 1,
    "purpose": "build founder credibility",
    "audience": ["business owners"],
    "message_focus": "make the idea immediate and memorable",
    "tone": ["confident"],
    "cta_direction": "invite viewers to explore NRC",
    "required_context": [],
    "constraints": [],
}

VALID_RESPONSE = {
    "strategy": VALID_STRATEGY,
    "outputs": [VALID_OUTPUT],
    "excluded_outputs": [{"output_type": "linkedin_post", "reason": "not enough project detail"}],
}


def _json(data):
    return json.dumps(data)


def test_parses_a_well_formed_response():
    parsed = parse_content_plan_response(_json(VALID_RESPONSE))

    assert parsed.strategy.central_message == VALID_STRATEGY["central_message"]
    assert len(parsed.outputs) == 1
    assert parsed.outputs[0].output_type == "instagram_reel_caption"
    assert len(parsed.excluded_outputs) == 1


def test_output_id_is_placeholder_before_service_assignment():
    parsed = parse_content_plan_response(_json(VALID_RESPONSE))

    assert parsed.outputs[0].output_id == "pending"


def test_parses_multiple_outputs():
    data = dict(VALID_RESPONSE)
    data["outputs"] = [VALID_OUTPUT, {**VALID_OUTPUT, "output_type": "linkedin_post", "priority": 2}]
    data["excluded_outputs"] = []

    parsed = parse_content_plan_response(_json(data))

    assert len(parsed.outputs) == 2


def test_rejects_invalid_json():
    with pytest.raises(MalformedContentPlanResponseError):
        parse_content_plan_response("{not valid")


def test_rejects_non_object_json():
    with pytest.raises(MalformedContentPlanResponseError):
        parse_content_plan_response("[1, 2]")


def test_rejects_missing_top_level_field():
    data = dict(VALID_RESPONSE)
    del data["excluded_outputs"]

    with pytest.raises(ContentPlanSchemaMismatchError, match="excluded_outputs"):
        parse_content_plan_response(_json(data))


def test_rejects_extra_top_level_field():
    data = dict(VALID_RESPONSE)
    data["extra"] = "nope"

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_rejects_strategy_missing_a_field():
    data = dict(VALID_RESPONSE)
    strategy = dict(VALID_STRATEGY)
    del strategy["central_message"]
    data["strategy"] = strategy

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_rejects_strategy_with_extra_field():
    data = dict(VALID_RESPONSE)
    data["strategy"] = {**VALID_STRATEGY, "extra_field": "x"}

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_rejects_output_missing_a_field():
    data = dict(VALID_RESPONSE)
    output = dict(VALID_OUTPUT)
    del output["priority"]
    data["outputs"] = [output]

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_rejects_non_list_outputs():
    data = dict(VALID_RESPONSE)
    data["outputs"] = "not a list"

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_rejects_excluded_output_missing_reason():
    data = dict(VALID_RESPONSE)
    data["excluded_outputs"] = [{"output_type": "linkedin_post"}]

    with pytest.raises(ContentPlanSchemaMismatchError):
        parse_content_plan_response(_json(data))


def test_accepts_empty_outputs_and_excluded_outputs_lists_structurally():
    # Business-rule validation (at least one output) is a separate
    # concern — see content_plan_validation.py; the parser only enforces
    # shape.
    data = dict(VALID_RESPONSE)
    data["outputs"] = []
    data["excluded_outputs"] = []

    parsed = parse_content_plan_response(_json(data))

    assert parsed.outputs == []
    assert parsed.excluded_outputs == []


def test_never_evals_input():
    malicious = "__import__('os').system('echo pwned')"

    with pytest.raises(MalformedContentPlanResponseError):
        parse_content_plan_response(malicious)
