import pytest

from src.ai.clarification_models import (
    ClarificationContext,
    ClarificationDecision,
    ClarificationDecisionType,
    ClarificationQuestion,
    ContextField,
    ContextSource,
)
from src.ai.errors import ClarificationResponseSchemaMismatchError

NOW = "2026-01-01T00:00:00+00:00"


# --- ContextField ---------------------------------------------------------


def test_context_field_round_trips_through_dict():
    field = ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW)

    assert ContextField.from_dict(field.to_dict()) == field


def test_context_field_from_dict_accepts_list_value_when_expected():
    data = {"value": ["instagram", "linkedin"], "source": "user", "updated_at": NOW}

    field = ContextField.from_dict(data, expect_list=True)

    assert field.value == ["instagram", "linkedin"]


def test_context_field_from_dict_rejects_non_string_scalar_value():
    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ContextField.from_dict({"value": 123, "source": "user", "updated_at": NOW})


def test_context_field_from_dict_rejects_unrecognized_source():
    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ContextField.from_dict({"value": "NRC", "source": "guess", "updated_at": NOW})


def test_context_field_from_dict_rejects_missing_updated_at():
    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ContextField.from_dict({"value": "NRC", "source": "user"})


# --- ClarificationContext: to_dict / from_dict --------------------------


def test_empty_context_round_trips():
    context = ClarificationContext()

    restored = ClarificationContext.from_dict(context.to_dict())

    assert restored == context


def test_context_with_values_round_trips():
    context = ClarificationContext(
        brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW),
        platforms=ContextField(value=["instagram"], source=ContextSource.USER, updated_at=NOW),
        factual_context={"event": "launch"},
        user_preferences={"style": "premium"},
        unresolved=["tone"],
    )

    restored = ClarificationContext.from_dict(context.to_dict())

    assert restored == context


def test_from_dict_rejects_non_dict_input():
    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ClarificationContext.from_dict("not a dict")


def test_from_dict_rejects_non_string_factual_context_values():
    data = ClarificationContext().to_dict()
    data["factual_context"] = {"event": 123}

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ClarificationContext.from_dict(data)


def test_from_dict_rejects_non_list_unresolved():
    data = ClarificationContext().to_dict()
    data["unresolved"] = "not a list"

    with pytest.raises(ClarificationResponseSchemaMismatchError):
        ClarificationContext.from_dict(data)


def test_from_dict_defaults_missing_optional_fields():
    data = {"version": 1}

    context = ClarificationContext.from_dict(data)

    assert context.brand_name is None
    assert context.factual_context == {}
    assert context.unresolved == []


# --- ClarificationContext.apply_updates -----------------------------------


def test_apply_updates_sets_a_previously_unknown_field():
    context = ClarificationContext()

    updated = context.apply_updates(
        {"brand_name": "NRC"}, unresolved=[], updated_at=NOW, source=ContextSource.USER
    )

    assert updated.brand_name.value == "NRC"
    assert updated.brand_name.source is ContextSource.USER


def test_apply_updates_leaves_field_untouched_when_update_is_empty_string():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    updated = context.apply_updates({"brand_name": ""}, unresolved=[], updated_at=NOW, source=ContextSource.USER)

    assert updated.brand_name.value == "NRC"


def test_apply_updates_lets_a_new_user_answer_override_a_prior_user_answer():
    # A correction: the user said "intro" earlier, now says "launch".
    context = ClarificationContext(
        content_type=ContextField(value="intro", source=ContextSource.USER, updated_at=NOW)
    )

    updated = context.apply_updates(
        {"content_type": "launch"}, unresolved=[], updated_at="2026-01-02T00:00:00+00:00", source=ContextSource.USER
    )

    assert updated.content_type.value == "launch"


def test_apply_updates_never_lets_inference_silently_overwrite_an_explicit_user_answer():
    context = ClarificationContext(
        tone=ContextField(value="premium but approachable", source=ContextSource.USER, updated_at=NOW)
    )

    updated = context.apply_updates(
        {"tone": "corporate"}, unresolved=[], updated_at=NOW, source=ContextSource.INFERENCE
    )

    assert updated.tone.value == "premium but approachable"
    assert updated.tone.source is ContextSource.USER


def test_apply_updates_allows_inference_to_set_a_field_with_no_prior_value():
    context = ClarificationContext()

    updated = context.apply_updates(
        {"content_type": "founder intro video"}, unresolved=[], updated_at=NOW, source=ContextSource.INFERENCE
    )

    assert updated.content_type.value == "founder intro video"
    assert updated.content_type.source is ContextSource.INFERENCE


def test_apply_updates_merges_factual_context_and_user_preferences_without_dropping_prior_keys():
    context = ClarificationContext(factual_context={"event": "launch"}, user_preferences={"style": "premium"})

    updated = context.apply_updates(
        {"factual_context": {"location": "SF"}, "user_preferences": {"cta": "book a call"}},
        unresolved=[],
        updated_at=NOW,
        source=ContextSource.USER,
    )

    assert updated.factual_context == {"event": "launch", "location": "SF"}
    assert updated.user_preferences == {"style": "premium", "cta": "book a call"}


def test_apply_updates_replaces_unresolved_wholesale():
    context = ClarificationContext(unresolved=["tone", "audience"])

    updated = context.apply_updates({}, unresolved=["audience"], updated_at=NOW, source=ContextSource.USER)

    assert updated.unresolved == ["audience"]


def test_apply_updates_sets_platforms_list():
    context = ClarificationContext()

    updated = context.apply_updates(
        {"platforms": ["instagram", "linkedin"]}, unresolved=[], updated_at=NOW, source=ContextSource.USER
    )

    assert updated.platforms.value == ["instagram", "linkedin"]


def test_apply_updates_protects_explicit_platforms_from_inference_override():
    context = ClarificationContext(
        platforms=ContextField(value=["instagram"], source=ContextSource.USER, updated_at=NOW)
    )

    updated = context.apply_updates(
        {"platforms": ["linkedin"]}, unresolved=[], updated_at=NOW, source=ContextSource.INFERENCE
    )

    assert updated.platforms.value == ["instagram"]


def test_apply_updates_does_not_mutate_the_original_context():
    context = ClarificationContext(brand_name=ContextField(value="NRC", source=ContextSource.USER, updated_at=NOW))

    context.apply_updates({"brand_name": "Other"}, unresolved=[], updated_at=NOW, source=ContextSource.USER)

    assert context.brand_name.value == "NRC"  # unchanged; frozen dataclass, new instance returned


# --- ClarificationDecision / ClarificationQuestion ------------------------


def test_clarification_decision_ask_question_carries_a_question():
    decision = ClarificationDecision(
        decision=ClarificationDecisionType.ASK_QUESTION,
        context_sufficient=False,
        question=ClarificationQuestion(text="Which platform?", purpose="clarify platform", target_field="platforms"),
        context_updates={},
        remaining_uncertainties=["platform"],
        confidence=0.6,
    )

    assert decision.question.text == "Which platform?"


def test_clarification_decision_continue_has_no_question():
    decision = ClarificationDecision(
        decision=ClarificationDecisionType.CONTINUE,
        context_sufficient=True,
        question=None,
        context_updates={},
        remaining_uncertainties=[],
        confidence=0.9,
    )

    assert decision.question is None
