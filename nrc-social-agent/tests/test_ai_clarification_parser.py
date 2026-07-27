import json

import pytest

from src.ai.clarification_models import ClarificationDecisionType
from src.ai.clarification_parser import parse_clarification_response
from src.ai.errors import ClarificationResponseSchemaMismatchError, MalformedClarificationResponseError

_EMPTY_CONTEXT_UPDATES = {
    "brand_name": "", "content_type": "", "objective": "", "audience": "", "message_focus": "",
    "tone": "", "call_to_action": "", "platforms": [], "factual_context": {}, "user_preferences": {},
}

ASK_QUESTION_RESPONSE = {
    "decision": "ASK_QUESTION",
    "context_sufficient": False,
    "question_text": "Is this mainly for founders, or a broader audience?",
    "question_purpose": "clarify audience",
    "question_target_field": "audience",
    "context_updates": _EMPTY_CONTEXT_UPDATES,
    "remaining_uncertainties": ["audience"],
    "confidence": 0.5,
}

CONTINUE_RESPONSE = {
    "decision": "CONTINUE",
    "context_sufficient": True,
    "question_text": "",
    "question_purpose": "",
    "question_target_field": "",
    "context_updates": _EMPTY_CONTEXT_UPDATES,
    "remaining_uncertainties": [],
    "confidence": 0.9,
}


def _json(data):
    return json.dumps(data)


def test_parses_a_well_formed_ask_question_response():
    decision = parse_clarification_response(_json(ASK_QUESTION_RESPONSE))

    assert decision.decision is ClarificationDecisionType.ASK_QUESTION
    assert decision.question.text == "Is this mainly for founders, or a broader audience?"
    assert decision.question.target_field == "audience"
    assert decision.confidence == 0.5


def test_parses_a_well_formed_continue_response():
    decision = parse_clarification_response(_json(CONTINUE_RESPONSE))

    assert decision.decision is ClarificationDecisionType.CONTINUE
    assert decision.question is None
    assert decision.context_sufficient is True


def test_parses_context_updates_with_values():
    data = dict(CONTINUE_RESPONSE)
    data["context_updates"] = {**_EMPTY_CONTEXT_UPDATES, "brand_name": "NRC", "platforms": ["instagram"]}

    decision = parse_clarification_response(_json(data))

    assert decision.context_updates["brand_name"] == "NRC"
    assert decision.context_updates["platforms"] == ["instagram"]


def test_rejects_invalid_json():
    with pytest.raises(MalformedClarificationResponseError):
        parse_clarification_response("{not valid")


def test_rejects_non_object_json():
    with pytest.raises(MalformedClarificationResponseError):
        parse_clarification_response("[1, 2]")


def test_rejects_missing_required_field():
    data = dict(CONTINUE_RESPONSE)
    del data["confidence"]

    with pytest.raises(ClarificationResponseSchemaMismatchError, match="confidence"):
        parse_clarification_response(_json(data))


def test_rejects_extra_field():
    data = dict(CONTINUE_RESPONSE)
    data["extra"] = "nope"

    with pytest.raises(ClarificationResponseSchemaMismatchError, match="extra"):
        parse_clarification_response(_json(data))


def test_rejects_invalid_decision_value():
    data = dict(CONTINUE_RESPONSE)
    data["decision"] = "MAYBE"

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_non_bool_context_sufficient():
    data = dict(CONTINUE_RESPONSE)
    data["context_sufficient"] = "yes"

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_ask_question_with_empty_question_text():
    data = dict(ASK_QUESTION_RESPONSE)
    data["question_text"] = ""

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_invalid_target_field():
    data = dict(ASK_QUESTION_RESPONSE)
    data["question_target_field"] = "not_a_real_field"

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.1, "0.5", True])
def test_rejects_out_of_range_or_wrong_typed_confidence(bad_confidence):
    data = dict(CONTINUE_RESPONSE)
    data["confidence"] = bad_confidence

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_non_list_remaining_uncertainties():
    data = dict(CONTINUE_RESPONSE)
    data["remaining_uncertainties"] = "not a list"

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_context_updates_missing_a_field():
    data = dict(CONTINUE_RESPONSE)
    updates = dict(_EMPTY_CONTEXT_UPDATES)
    del updates["brand_name"]
    data["context_updates"] = updates

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_context_updates_with_extra_field():
    data = dict(CONTINUE_RESPONSE)
    data["context_updates"] = {**_EMPTY_CONTEXT_UPDATES, "made_up_field": "x"}

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_context_updates_with_non_string_scalar():
    data = dict(CONTINUE_RESPONSE)
    data["context_updates"] = {**_EMPTY_CONTEXT_UPDATES, "brand_name": 123}

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_context_updates_with_non_list_platforms():
    data = dict(CONTINUE_RESPONSE)
    data["context_updates"] = {**_EMPTY_CONTEXT_UPDATES, "platforms": "instagram"}

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_rejects_context_updates_with_non_string_dict_values():
    data = dict(CONTINUE_RESPONSE)
    data["context_updates"] = {**_EMPTY_CONTEXT_UPDATES, "factual_context": {"event": 123}}

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        parse_clarification_response(_json(data))


def test_never_evals_input():
    malicious = "__import__('os').system('echo pwned')"

    with pytest.raises(MalformedClarificationResponseError):
        parse_clarification_response(malicious)
