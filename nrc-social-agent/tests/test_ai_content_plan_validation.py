import pytest

from src.ai.content_plan_models import ExcludedOutput, PlanStrategy, PlannedOutput
from src.ai.content_plan_parser import ParsedContentPlan
from src.ai.content_plan_validation import validate_content_plan
from src.ai.errors import ContentPlanValidationFailedError

GROUNDING_TEXT = (
    "A founder speaking to camera in a premium, approachable setting. "
    "NRC brand signals visible. Instagram video introduction credibility."
)


def _strategy(**overrides):
    defaults = dict(
        content_type="founder_introduction",
        primary_objective="build founder credibility for NRC",
        # Deliberately shares vocabulary with GROUNDING_TEXT ("founder",
        # "camera", "credibility", "premium") — see
        # _shares_grounded_content's 4+-letter-word overlap heuristic.
        central_message="The founder's on-camera introduction should carry premium credibility forward",
        brand_positioning="premium, connected",
        cta_direction="invite viewers to explore NRC",
        audience=["business owners"],
        tone_direction=["confident", "premium"],
        factual_constraints=["brand: NRC"],
        avoid=["performance claims"],
    )
    defaults.update(overrides)
    return PlanStrategy(**defaults)


def _output(**overrides):
    defaults = dict(
        output_id="pending",
        output_type="instagram_reel_caption",
        priority=1,
        purpose="build founder credibility on Instagram",
        message_focus="make the NRC founder story immediate and memorable",
        cta_direction="invite viewers to explore NRC",
        audience=["business owners"],
        tone=["confident"],
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


def _plan(*, strategy=None, outputs=None, excluded_outputs=None):
    return ParsedContentPlan(
        strategy=strategy or _strategy(),
        outputs=outputs if outputs is not None else [_output()],
        excluded_outputs=excluded_outputs or [],
    )


def _validate(plan, *, max_outputs=3):
    validate_content_plan(plan, max_outputs=max_outputs, grounding_text=GROUNDING_TEXT)


# --- happy path ----------------------------------------------------------


def test_accepts_a_well_grounded_single_output_plan():
    _validate(_plan())  # must not raise


def test_accepts_a_well_grounded_multi_output_plan_with_distinct_roles():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1, message_focus="make the NRC founder story immediate"),
        _output(
            output_type="linkedin_post", priority=2,
            message_focus="explain the NRC business reasoning and founder perspective",
        ),
    ]
    _validate(_plan(outputs=outputs))  # must not raise


# --- output count -----------------------------------------------------


def test_rejects_zero_outputs():
    with pytest.raises(ContentPlanValidationFailedError, match="zero outputs"):
        _validate(_plan(outputs=[]))


def test_rejects_output_count_exceeding_maximum():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1),
        _output(output_type="linkedin_post", priority=2, message_focus="explain the NRC founder's reasoning in depth"),
        _output(output_type="threads_post", priority=3, message_focus="give a candid behind the scenes NRC take"),
    ]
    with pytest.raises(ContentPlanValidationFailedError, match="exceeding"):
        _validate(_plan(outputs=outputs), max_outputs=2)


def test_allows_output_count_at_the_maximum():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1),
        _output(output_type="linkedin_post", priority=2, message_focus="explain the NRC founder's reasoning in depth"),
    ]
    _validate(_plan(outputs=outputs), max_outputs=2)  # must not raise


# --- output types and priorities ------------------------------------------


def test_rejects_unsupported_output_type():
    with pytest.raises(ContentPlanValidationFailedError, match="unsupported output_type"):
        _validate(_plan(outputs=[_output(output_type="tiktok_script")]))


def test_rejects_duplicate_output_types():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1),
        _output(output_type="instagram_reel_caption", priority=2, message_focus="a second distinct angle on NRC"),
    ]
    with pytest.raises(ContentPlanValidationFailedError, match="duplicate output types"):
        _validate(_plan(outputs=outputs))


def test_rejects_invalid_priority_value():
    with pytest.raises(ContentPlanValidationFailedError, match="invalid priority"):
        _validate(_plan(outputs=[_output(priority=7)]))


def test_rejects_zero_primary_outputs():
    with pytest.raises(ContentPlanValidationFailedError, match="priority 1"):
        _validate(_plan(outputs=[_output(priority=2)]))


def test_rejects_multiple_primary_outputs():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1),
        _output(output_type="linkedin_post", priority=1, message_focus="a distinct NRC founder angle for LinkedIn"),
    ]
    with pytest.raises(ContentPlanValidationFailedError, match="priority 1"):
        _validate(_plan(outputs=outputs))


# --- Milestone 11A: priority-1 must support generation ---------------------
#
# Authoring-contract invariant: the planning layer must never select an
# output type the current generation layer cannot generate. Registered-
# but-unsupported types (e.g. instagram_feed_caption) remain fully valid
# to propose at a lower priority; they simply cannot become priority 1.


def test_supported_output_type_remains_selectable_as_priority_one():
    _validate(_plan(outputs=[_output(output_type="instagram_reel_caption", priority=1)]))  # must not raise


def test_unsupported_output_type_remains_registered_and_selectable_at_lower_priority():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1),
        _output(
            output_type="instagram_feed_caption", priority=2,
            message_focus="a distinct, lower-priority NRC feed angle",
        ),
    ]
    _validate(_plan(outputs=outputs))  # must not raise — instagram_feed_caption is still a valid registry entry


def test_unsupported_output_type_cannot_become_priority_one():
    with pytest.raises(ContentPlanValidationFailedError, match="does not support generation"):
        _validate(_plan(outputs=[_output(output_type="instagram_feed_caption", priority=1)]))


def test_unsupported_priority_one_failure_message_is_informative():
    # "Planner failures remain informative" — the raised message names
    # the exact offending output_type and points toward a corrected choice,
    # since content_planning_service.py forwards this message verbatim
    # into its one bounded regeneration-attempt prompt.
    with pytest.raises(ContentPlanValidationFailedError) as excinfo:
        _validate(_plan(outputs=[_output(output_type="instagram_feed_caption", priority=1)]))
    message = str(excinfo.value)
    assert "instagram_feed_caption" in message
    assert "instagram_reel_caption" in message  # names a generation-supported alternative


def test_unsupported_planning_only_output_types_are_rejected_regardless_of_priority_slot():
    # instagram_carousel_plan/linkedin_post/threads_post/website_* are all
    # supports_generation=False today — none may occupy priority 1.
    for unsupported_type in (
        "instagram_carousel_plan", "linkedin_post", "threads_post",
        "website_portfolio_entry", "website_case_study_outline",
    ):
        with pytest.raises(ContentPlanValidationFailedError, match="does not support generation"):
            _validate(_plan(outputs=[_output(output_type=unsupported_type, priority=1)]))


# --- output distinctiveness -----------------------------------------------


def test_rejects_outputs_with_identical_message_focus():
    outputs = [
        _output(output_type="instagram_reel_caption", priority=1, message_focus="make the NRC founder story immediate"),
        _output(output_type="linkedin_post", priority=2, message_focus="make the NRC founder story immediate"),
    ]
    with pytest.raises(ContentPlanValidationFailedError, match="distinct role"):
        _validate(_plan(outputs=outputs))


def test_rejects_output_with_too_short_purpose():
    with pytest.raises(ContentPlanValidationFailedError, match="purpose"):
        _validate(_plan(outputs=[_output(purpose="short")]))


def test_rejects_output_with_too_short_message_focus():
    with pytest.raises(ContentPlanValidationFailedError, match="message_focus"):
        _validate(_plan(outputs=[_output(message_focus="short")]))


# --- excluded outputs -------------------------------------------------


def test_rejects_excluded_output_with_unsupported_type():
    with pytest.raises(ContentPlanValidationFailedError, match="unsupported output_type"):
        _validate(_plan(excluded_outputs=[ExcludedOutput(output_type="tiktok_script", reason="not applicable")]))


def test_rejects_conflicting_proposed_and_excluded_output_type():
    with pytest.raises(ContentPlanValidationFailedError, match="conflicting"):
        _validate(
            _plan(
                outputs=[_output(output_type="instagram_reel_caption")],
                excluded_outputs=[ExcludedOutput(output_type="instagram_reel_caption", reason="not enough context")],
            )
        )


def test_allows_a_non_conflicting_excluded_output():
    _validate(
        _plan(excluded_outputs=[ExcludedOutput(output_type="website_case_study_outline", reason="no project detail")])
    )  # must not raise


# --- final-copy leakage -----------------------------------------------


def test_rejects_output_field_containing_a_hashtag():
    with pytest.raises(ContentPlanValidationFailedError, match="hashtag"):
        _validate(_plan(outputs=[_output(message_focus="Make it pop with #NRCfounder energy today")]))


def test_rejects_output_field_containing_a_quoted_line():
    with pytest.raises(ContentPlanValidationFailedError, match="quoted"):
        _validate(_plan(outputs=[_output(purpose='Open with "Most businesses are posting but forgotten" hook')]))


def test_rejects_field_reading_as_multi_sentence_prose():
    prose = (
        "NRC helps you grow. Our team builds brand systems. "
        "Clients see results fast. Book a call today."
    )
    with pytest.raises(ContentPlanValidationFailedError, match="multi-sentence"):
        _validate(_plan(strategy=_strategy(central_message=prose)))


def test_rejects_field_exceeding_length_limit():
    long_text = "NRC founder credibility " * 20
    with pytest.raises(ContentPlanValidationFailedError, match="finished copy"):
        _validate(_plan(strategy=_strategy(central_message=long_text)))


# --- internal term leakage -----------------------------------------------


def test_rejects_field_exposing_internal_technical_term():
    with pytest.raises(ContentPlanValidationFailedError, match="internal/technical term"):
        _validate(_plan(strategy=_strategy(cta_direction="Reference the json schema output for the CTA")))


# --- generic-plan detection -------------------------------------------


def test_rejects_known_generic_central_message():
    with pytest.raises(ContentPlanValidationFailedError, match="generic phrase"):
        _validate(_plan(strategy=_strategy(central_message="increase engagement")))


def test_rejects_known_generic_primary_objective():
    with pytest.raises(ContentPlanValidationFailedError, match="generic phrase"):
        _validate(_plan(strategy=_strategy(primary_objective="increase engagement")))


def test_rejects_central_message_ungrounded_in_provided_context():
    with pytest.raises(ContentPlanValidationFailedError, match="generic"):
        _validate(_plan(strategy=_strategy(central_message="Every business deserves better online visibility today")))


def test_rejects_unsupported_content_type():
    with pytest.raises(ContentPlanValidationFailedError, match="unsupported content_type"):
        _validate(_plan(strategy=_strategy(content_type="email_campaign")))


def test_allows_other_content_type():
    _validate(_plan(strategy=_strategy(content_type="other", content_type_description="A hybrid founder/product piece")))


# --- invented factual claims (numbers) -------------------------------


def test_rejects_invented_number_not_present_in_grounding_text():
    with pytest.raises(ContentPlanValidationFailedError, match="invented factual claim"):
        _validate(_plan(strategy=_strategy(factual_constraints=["NRC has served 500 clients"])))


def test_allows_a_number_that_appears_in_the_grounding_text():
    grounding_with_number = GROUNDING_TEXT + " Founded in 2020."
    plan = _plan(strategy=_strategy(factual_constraints=["Founded in 2020"]))

    validate_content_plan(plan, max_outputs=3, grounding_text=grounding_with_number)  # must not raise
