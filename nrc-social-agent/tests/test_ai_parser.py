import pytest

from src.ai.errors import AnalysisResponseSchemaMismatchError, MalformedAnalysisResponseError
from src.ai.models import AnalysisResult
from src.ai.parser import parse_analysis_response

VALID = {
    "summary": "A dog on a beach.",
    "visible_subjects": ["a dog", "the ocean"],
    "visual_style": ["bright", "natural light"],
    "dominant_themes": ["outdoors"],
    "brand_signals": [],
    "content_opportunities": ["pet-friendly messaging"],
    "quality_observations": ["slightly overexposed"],
    "safety_notes": [],
}


def _json(data):
    import json

    return json.dumps(data)


def test_parse_analysis_response_accepts_well_formed_response():
    result = parse_analysis_response(_json(VALID))

    assert isinstance(result, AnalysisResult)
    assert result.summary == "A dog on a beach."
    assert result.visible_subjects == ["a dog", "the ocean"]
    assert result.safety_notes == []


def test_parse_analysis_response_rejects_invalid_json():
    with pytest.raises(MalformedAnalysisResponseError):
        parse_analysis_response("{not valid json")


def test_parse_analysis_response_rejects_non_object_json():
    with pytest.raises(MalformedAnalysisResponseError):
        parse_analysis_response("[1, 2, 3]")


def test_parse_analysis_response_rejects_missing_required_field():
    data = dict(VALID)
    del data["summary"]

    with pytest.raises(AnalysisResponseSchemaMismatchError, match="summary"):
        parse_analysis_response(_json(data))


def test_parse_analysis_response_rejects_extra_field():
    data = dict(VALID)
    data["caption"] = "a made-up caption"

    with pytest.raises(AnalysisResponseSchemaMismatchError, match="caption"):
        parse_analysis_response(_json(data))


def test_parse_analysis_response_rejects_wrong_typed_summary():
    data = dict(VALID)
    data["summary"] = 123

    with pytest.raises(AnalysisResponseSchemaMismatchError, match="summary"):
        parse_analysis_response(_json(data))


def test_parse_analysis_response_rejects_non_list_array_field():
    data = dict(VALID)
    data["visible_subjects"] = "a dog"

    with pytest.raises(AnalysisResponseSchemaMismatchError, match="visible_subjects"):
        parse_analysis_response(_json(data))


def test_parse_analysis_response_rejects_array_field_with_non_string_items():
    data = dict(VALID)
    data["visible_subjects"] = ["a dog", 42]

    with pytest.raises(AnalysisResponseSchemaMismatchError, match="visible_subjects"):
        parse_analysis_response(_json(data))


def test_parse_analysis_response_never_evals_input():
    # A payload that would execute arbitrary code if eval() were used.
    malicious = "__import__('os').system('echo pwned')"

    with pytest.raises(MalformedAnalysisResponseError):
        parse_analysis_response(malicious)
