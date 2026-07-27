import pytest

from src.ai.errors import InvalidClarificationQuestionError
from src.ai.question_validation import MAX_QUESTION_LENGTH, validate_question_text


def test_accepts_a_natural_grounded_question():
    validate_question_text(
        "Is the main goal to build credibility for NRC, or drive discovery-call enquiries?",
        previous_questions=[],
    )  # must not raise


def test_rejects_empty_question():
    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text("   ", previous_questions=[])


def test_rejects_question_exceeding_length_limit():
    text = "Is this " + "really " * 100 + "for founders?"
    assert len(text) > MAX_QUESTION_LENGTH

    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text(text, previous_questions=[])


def test_rejects_question_with_more_than_one_question_mark():
    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text("What is the brand? What is the tone?", previous_questions=[])


def test_rejects_numbered_questionnaire():
    text = "1. What is the brand?\n2. What is the objective?\n3. Who is the audience?"

    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text(text, previous_questions=[])


@pytest.mark.parametrize("banned", ["Please confirm the JSON schema fields.", "What does the workflow state show?", "Is Claude able to help here?"])
def test_rejects_question_exposing_internal_technical_terms(banned):
    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text(banned, previous_questions=[])


@pytest.mark.parametrize("field_name", ["brand_name", "message_focus", "call_to_action", "context_sufficient"])
def test_rejects_question_containing_internal_field_names(field_name):
    text = f"Can you confirm the {field_name} value?"

    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text(text, previous_questions=[])


def test_rejects_materially_identical_question():
    previous = "Is the main goal to build credibility for NRC, or drive discovery-call enquiries?"
    nearly_identical = "Is the main goal to build credibility for NRC or drive discovery call enquiries?"

    with pytest.raises(InvalidClarificationQuestionError):
        validate_question_text(nearly_identical, previous_questions=[previous])


def test_accepts_a_genuinely_different_question_from_previous_ones():
    previous = "Is the main goal to build credibility for NRC, or drive discovery-call enquiries?"
    different = "Should the voice feel more authoritative and editorial, or personal and conversational?"

    validate_question_text(different, previous_questions=[previous])  # must not raise


def test_allows_a_question_with_illustrative_options_and_one_question_mark():
    validate_question_text(
        "Should this feel more like a confident founder introduction, a behind-the-scenes moment, or something else?",
        previous_questions=[],
    )  # must not raise
