import pytest

from src.ai.draft_models import InstagramReelCaptionContent
from src.ai.draft_validation import MAX_EMOJI_COUNT, validate_instagram_reel_caption
from src.ai.errors import DraftValidationFailedError

GROUNDING_TEXT = (
    "A founder speaking to camera in a premium, approachable setting. "
    "NRC brand signals visible. Instagram video introduction credibility "
    "central message: the founder's on-camera introduction carries premium credibility forward."
)


def _content(**overrides):
    defaults = dict(
        caption="The founder's on-camera introduction carries premium credibility forward for NRC.",
        hashtags=[],
        cta=None,
    )
    defaults.update(overrides)
    return InstagramReelCaptionContent(**defaults)


def _validate(content, *, max_caption_length=2200, max_hashtags=5, grounding_text=GROUNDING_TEXT):
    validate_instagram_reel_caption(
        content, max_caption_length=max_caption_length, max_hashtags=max_hashtags, grounding_text=grounding_text
    )


# --- happy path ------------------------------------------------------------


def test_accepts_a_well_grounded_caption():
    _validate(_content())  # must not raise


def test_accepts_a_caption_with_a_supported_cta_and_hashtags():
    _validate(_content(cta="Watch the founder's introduction.", hashtags=["nrc", "founder"]))  # must not raise


# --- length ------------------------------------------------------------


def test_rejects_caption_below_minimum_length():
    with pytest.raises(DraftValidationFailedError, match="below the minimum meaningful length"):
        _validate(_content(caption="Too short."))


def test_rejects_caption_exceeding_maximum_length():
    long_caption = "NRC founder credibility on camera today. " * 60
    with pytest.raises(DraftValidationFailedError, match="exceeding the configured maximum"):
        _validate(_content(caption=long_caption), max_caption_length=100)


# --- markdown / placeholder text ----------------------------------------


def test_rejects_caption_with_markdown_fence():
    with pytest.raises(DraftValidationFailedError, match="markdown code fence"):
        _validate(_content(caption="```\nThe founder's premium credibility on camera.\n```"))


def test_rejects_caption_with_placeholder_text():
    with pytest.raises(DraftValidationFailedError, match="placeholder/template text"):
        _validate(_content(caption="[Insert founder name]'s premium credibility on camera today."))


# --- internal terminology leakage ---------------------------------------


def test_rejects_caption_exposing_internal_technical_term():
    with pytest.raises(DraftValidationFailedError, match="internal/technical term"):
        _validate(_content(caption="This caption follows the content plan and json schema for NRC's founder."))


# --- generic agency language ---------------------------------------------


def test_rejects_known_generic_phrase():
    with pytest.raises(DraftValidationFailedError, match="generic agency phrase"):
        _validate(_content(caption="We are excited to share our journey with NRC's premium founder credibility."))


def test_rejects_strategy_explanation_phrase():
    with pytest.raises(DraftValidationFailedError, match="explains its own strategy"):
        _validate(_content(caption="This post aims to build the founder's premium on-camera credibility for NRC."))


# --- groundedness and invented facts --------------------------------------


def test_rejects_caption_ungrounded_in_provided_context():
    with pytest.raises(DraftValidationFailedError, match="generic"):
        _validate(_content(caption="Every business deserves better online visibility and growth today."))


def test_rejects_invented_number_not_present_in_grounding_text():
    with pytest.raises(DraftValidationFailedError, match="invented factual claim"):
        _validate(_content(caption="NRC's founder has served 500 clients with premium on-camera credibility."))


def test_allows_a_number_that_appears_in_the_grounding_text():
    grounding_with_number = GROUNDING_TEXT + " Founded in 2020."
    content = _content(caption="NRC's founder, on camera since 2020, carries premium credibility forward.")

    _validate(content, grounding_text=grounding_with_number)  # must not raise


# --- emoji ---------------------------------------------------------------


def test_rejects_caption_exceeding_max_emoji_count():
    emoji_caption = "The founder's premium on-camera credibility for NRC " + "✨" * (MAX_EMOJI_COUNT + 1)
    with pytest.raises(DraftValidationFailedError, match="exceeding the maximum"):
        _validate(_content(caption=emoji_caption))


def test_allows_caption_at_max_emoji_count():
    emoji_caption = "The founder's premium on-camera credibility for NRC " + "✨" * MAX_EMOJI_COUNT
    _validate(_content(caption=emoji_caption))  # must not raise


def test_allows_zero_emoji():
    _validate(_content())  # must not raise — restraint is valid


# --- hashtags --------------------------------------------------------------


def test_rejects_hashtag_count_exceeding_maximum():
    with pytest.raises(DraftValidationFailedError, match="exceeding the configured maximum"):
        _validate(_content(hashtags=["a", "b", "c"]), max_hashtags=2)


def test_rejects_malformed_hashtag():
    with pytest.raises(DraftValidationFailedError, match="malformed hashtag"):
        _validate(_content(hashtags=["not a valid hashtag!"]))


def test_allows_hashtag_with_or_without_leading_hash():
    _validate(_content(hashtags=["#nrc", "founder"]))  # must not raise


# --- CTA -------------------------------------------------------------------


def test_rejects_generic_cta_phrase():
    with pytest.raises(DraftValidationFailedError, match="generic sales phrase"):
        _validate(_content(cta="DM us now"))


def test_rejects_cta_duplicated_verbatim_in_caption():
    caption = "The founder's premium on-camera credibility for NRC. Explore NRC's work."
    with pytest.raises(DraftValidationFailedError, match="duplicated verbatim"):
        _validate(_content(caption=caption, cta="Explore NRC's work."))


def test_allows_a_supported_non_generic_cta():
    _validate(_content(cta="Watch the founder's full introduction."))  # must not raise


def test_allows_no_cta():
    _validate(_content(cta=None))  # must not raise
