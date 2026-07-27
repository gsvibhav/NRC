import pytest

from src.ai.errors import (
    AnalysisDeserializationError,
    AnalysisVersionMismatchError,
    InvalidAnalysisStatusError,
)
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage


def _make_result(**overrides):
    defaults = dict(
        summary="A dog on a beach.",
        visible_subjects=["a dog"],
        visual_style=["bright"],
        dominant_themes=["outdoors"],
        brand_signals=[],
        content_opportunities=[],
        quality_observations=[],
        safety_notes=[],
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _make_document(**overrides):
    defaults = dict(
        analysis_id="an-1",
        workflow_id="wf-1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=AnalysisStatus.COMPLETED,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        media_type="photo",
        result=_make_result(),
        usage=AnalysisUsage(input_tokens=100, output_tokens=50),
    )
    defaults.update(overrides)
    return AnalysisDocument(**defaults)


# --- AnalysisResult -----------------------------------------------------


def test_analysis_result_round_trips_through_dict():
    result = _make_result()

    round_tripped = AnalysisResult.from_dict(result.to_dict())

    assert round_tripped == result


def test_analysis_result_from_dict_rejects_missing_field():
    data = _make_result().to_dict()
    del data["summary"]

    with pytest.raises(AnalysisDeserializationError):
        AnalysisResult.from_dict(data)


def test_analysis_result_from_dict_rejects_wrong_typed_array_field():
    data = _make_result().to_dict()
    data["visible_subjects"] = "not-a-list"

    with pytest.raises(AnalysisDeserializationError):
        AnalysisResult.from_dict(data)


# --- AnalysisUsage --------------------------------------------------------


def test_analysis_usage_round_trips_through_dict():
    usage = AnalysisUsage(input_tokens=10, output_tokens=20)

    assert AnalysisUsage.from_dict(usage.to_dict()) == usage


def test_analysis_usage_from_dict_rejects_non_int_tokens():
    with pytest.raises(AnalysisDeserializationError):
        AnalysisUsage.from_dict({"input_tokens": "10", "output_tokens": 20})


def test_analysis_usage_from_dict_rejects_bool_tokens():
    # bool is a subclass of int in Python — must be explicitly rejected.
    with pytest.raises(AnalysisDeserializationError):
        AnalysisUsage.from_dict({"input_tokens": True, "output_tokens": 20})


# --- AnalysisDocument ------------------------------------------------------


def test_analysis_document_round_trips_through_dict():
    document = _make_document()

    round_tripped = AnalysisDocument.from_dict(document.to_dict())

    assert round_tripped == document


def test_analysis_document_round_trips_with_no_result_or_usage():
    document = _make_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    round_tripped = AnalysisDocument.from_dict(document.to_dict())

    assert round_tripped.result is None
    assert round_tripped.usage is None
    assert round_tripped.status is AnalysisStatus.FAILED


def test_analysis_document_from_dict_rejects_unsupported_version():
    data = _make_document().to_dict()
    data["version"] = 999

    with pytest.raises(AnalysisVersionMismatchError):
        AnalysisDocument.from_dict(data)


def test_analysis_document_from_dict_rejects_missing_required_string_field():
    data = _make_document().to_dict()
    del data["workflow_id"]

    with pytest.raises(AnalysisDeserializationError):
        AnalysisDocument.from_dict(data)


def test_analysis_document_from_dict_rejects_unrecognized_status():
    data = _make_document().to_dict()
    data["status"] = "NOT_A_STATUS"

    with pytest.raises(InvalidAnalysisStatusError):
        AnalysisDocument.from_dict(data)


def test_analysis_document_from_dict_rejects_non_dict_input():
    with pytest.raises(AnalysisDeserializationError):
        AnalysisDocument.from_dict(["not", "a", "dict"])


def test_analysis_document_to_dict_never_includes_raw_chain_of_thought_field():
    # Defense-in-depth: the persisted shape has no field that could hold
    # thinking-block content, only the validated structured result.
    data = _make_document().to_dict()

    assert "thinking" not in data
    assert "raw_response" not in data
