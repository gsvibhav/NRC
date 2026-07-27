import pytest

from src.ai.draft_edit_validation import detect_unsupported_platform_switch, validate_draft_edit
from src.ai.draft_models import InstagramReelCaptionContent
from src.ai.errors import DraftEditValidationFailedError, DraftValidationFailedError

GROUNDING_TEXT = (
    "A founder speaking to camera in a premium, approachable setting. "
    "NRC brand signals visible. Instagram video introduction credibility "
    "central message: the founder's on-camera introduction carries premium credibility forward."
)

PARENT_CONTENT = {
    "caption": "The founder's on-camera introduction carries premium credibility forward for NRC today in full.",
    "hashtags": ["nrc", "founder"],
    "cta": "Explore NRC's work.",
}


def _content(**overrides):
    defaults = dict(
        caption="The founder's on-camera introduction carries premium credibility forward for NRC.",
        hashtags=[],
        cta=None,
    )
    defaults.update(overrides)
    return InstagramReelCaptionContent(**defaults)


def _validate(content, *, instruction, parent_content=PARENT_CONTENT, max_caption_length=2200, max_hashtags=5,
              grounding_text=GROUNDING_TEXT):
    validate_draft_edit(
        content, parent_content=parent_content, instruction=instruction,
        max_caption_length=max_caption_length, max_hashtags=max_hashtags, grounding_text=grounding_text,
    )


# --- detect_unsupported_platform_switch -----------------------------------


def test_detects_a_platform_switch_request():
    assert detect_unsupported_platform_switch("Turn this into a LinkedIn post") == "linkedin"


def test_detects_a_platform_switch_request_case_insensitively():
    assert detect_unsupported_platform_switch("please MAKE THIS A linkedin post") == "linkedin"


def test_does_not_flag_a_mere_style_reference_without_switch_phrasing():
    assert detect_unsupported_platform_switch("Make it punchier, like something you'd see on LinkedIn") is None


def test_does_not_flag_an_instruction_with_no_other_channel_mentioned():
    assert detect_unsupported_platform_switch("Make it shorter and remove the hashtags") is None


def test_detects_website_switch_request():
    assert detect_unsupported_platform_switch("Convert this to a website post") == "website"


# --- shared content-quality rules (delegated to draft_validation.py) -----


def test_accepts_a_well_grounded_revision():
    _validate(_content(), instruction="Make it more premium.")  # must not raise


def test_rejects_a_caption_below_minimum_length():
    # The shared base check raises draft_validation.py's own
    # DraftValidationFailedError (not the M7-specific subclass) — both
    # are treated identically as non-corrupting by draft_editing_service.py.
    with pytest.raises(DraftValidationFailedError):
        _validate(_content(caption="Too short."), instruction="Make it more premium.")


# --- remove hashtags -------------------------------------------------------


def test_rejects_hashtags_still_present_after_a_remove_hashtags_instruction():
    with pytest.raises(DraftEditValidationFailedError, match="remove hashtags"):
        _validate(_content(hashtags=["nrc"]), instruction="Please remove the hashtags.")


def test_accepts_empty_hashtags_after_a_remove_hashtags_instruction():
    _validate(_content(hashtags=[]), instruction="Please remove the hashtags.")  # must not raise


# --- remove CTA -------------------------------------------------------------


def test_rejects_cta_still_present_after_a_remove_cta_instruction():
    with pytest.raises(DraftEditValidationFailedError, match="remove the CTA"):
        _validate(_content(cta="Explore NRC's work."), instruction="Remove the CTA please.")


def test_accepts_no_cta_after_a_remove_cta_instruction():
    _validate(_content(cta=None), instruction="Remove the CTA please.")  # must not raise


# --- remove emoji -----------------------------------------------------------


def test_rejects_emoji_still_present_after_a_remove_emoji_instruction():
    with pytest.raises(DraftEditValidationFailedError, match="remove emoji"):
        _validate(
            _content(caption="The founder's on-camera introduction carries premium credibility forward ✨"),
            instruction="Remove the emojis.",
        )


def test_accepts_no_emoji_after_a_remove_emoji_instruction():
    _validate(_content(), instruction="Remove the emojis please.")  # must not raise


# --- shorten -----------------------------------------------------------


def test_rejects_a_revision_that_is_not_meaningfully_shorter():
    long_parent = {
        "caption": "The founder's on-camera introduction carries premium credibility forward for NRC " * 3,
        "hashtags": [], "cta": None,
    }
    with pytest.raises(DraftEditValidationFailedError, match="shorten"):
        _validate(
            _content(caption=long_parent["caption"][:-5]),  # trivially shorter, not meaningfully so
            instruction="Make it shorter.", parent_content=long_parent,
        )


def test_accepts_a_meaningfully_shorter_revision():
    long_parent = {
        "caption": "The founder's on-camera introduction carries premium credibility forward for NRC " * 3,
        "hashtags": [], "cta": None,
    }
    _validate(
        _content(caption="The founder's premium on-camera credibility for NRC."),
        instruction="Make it shorter.", parent_content=long_parent,
    )  # must not raise


# --- preserve a specific line ----------------------------------------------


def test_rejects_a_revision_missing_a_line_the_instruction_asked_to_preserve():
    with pytest.raises(DraftEditValidationFailedError, match="preserve a specific line"):
        _validate(
            _content(caption="A totally different premium NRC founder caption about credibility."),
            instruction='Keep this line exactly: "on-camera introduction"',
        )


def test_accepts_a_revision_that_preserves_the_requested_line():
    _validate(
        _content(caption="The founder's on-camera introduction carries premium credibility forward for NRC."),
        instruction='Keep this line exactly: "on-camera introduction"',
    )  # must not raise


def test_ignores_preserve_check_when_instruction_has_no_quoted_line():
    _validate(_content(), instruction="Keep the tone confident and premium.")  # must not raise
