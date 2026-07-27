"""Business-rule validation for a freshly-parsed draft, run before it is
ever persisted (see draft_generation_service.py). Structured output and
prompt instructions already push Claude toward a good draft, but this is
the enforced, code-level backstop — a draft failing any rule here is a
controlled failure (one regeneration attempt, then fail safely), never
silently repaired or persisted as ready for review.

Same two-family split as content_plan_validation.py:
1. **Structural rules** — length, hashtag format/count, CTA duplication.
   Exact and reliable.
2. **Content-quality heuristics** — generic-copy detection, groundedness,
   invented-number detection, internal-terminology leakage, placeholder
   text. Necessarily imperfect pattern checks, not semantic judgment — see
   README.md's documented limitations (the same ones already noted for
   content_plan_validation.py apply here for the same reasons).
"""

from __future__ import annotations

import re

from .draft_models import InstagramReelCaptionContent
from .errors import DraftValidationFailedError

MIN_MEANINGFUL_CAPTION_LENGTH = 20

# A restrained, premium brand may reasonably use zero emoji — this is a
# ceiling against visible excess, not a target.
MAX_EMOJI_COUNT = 3

_HASHTAG_PATTERN = re.compile(r"^#?[A-Za-z0-9_]+$")

_MARKDOWN_FENCE_PATTERN = re.compile(r"```")

_PLACEHOLDER_PATTERNS = (
    "[insert", "{{", "<insert", "lorem ipsum", "[brand name]", "[caption]",
    "[your", "insert your", "insert brand", "todo:",
)

_BANNED_SUBSTRINGS = (
    "json", "schema", "s3 ", "workflow", "content plan", "clarification context",
    "output type", "confidence score", "api key", "anthropic", "claude",
    "database", "bucket", "internal field",
)

# The brief's own cited examples of generic agency copy — an exact
# (case-insensitive) substring match is a strong signal the draft wasn't
# actually grounded in this asset.
_GENERIC_PHRASES = (
    "we are excited to share our journey",
    "innovation meets creativity",
    "taking brands to the next level",
    "stay tuned for more",
    "your vision, our passion",
    "we help brands grow",
)

_GENERIC_CTA_PHRASES = (
    "dm us now", "book a call today", "click the link in bio", "contact us", "follow for more",
)

_STRATEGY_EXPLANATION_PHRASES = (
    "this post aims to", "the strategy is", "in this caption we", "this caption is designed to",
    "the goal of this post",
)

_STOPWORDS = frozenset(
    "a an the of and or to in on for with is are this that from as by "
    "your our their its it be will can into about across than then over "
    "under out up down at not no yes just also more most very".split()
)
_NUMBER_PATTERN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")

# Rough coverage of common emoji code point ranges — good enough for a
# "how many emoji" count, not a complete Unicode-emoji classifier.
_EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


def validate_instagram_reel_caption(
    content: InstagramReelCaptionContent, *, max_caption_length: int, max_hashtags: int, grounding_text: str
) -> None:
    """Raises DraftValidationFailedError with a specific reason on any
    rule violation. Returns None (silently) if the draft passes."""

    caption = content.caption.strip()

    if len(caption) < MIN_MEANINGFUL_CAPTION_LENGTH:
        raise DraftValidationFailedError(
            f"caption is only {len(caption)} characters — below the minimum meaningful length "
            f"({MIN_MEANINGFUL_CAPTION_LENGTH})"
        )
    if len(caption) > max_caption_length:
        raise DraftValidationFailedError(
            f"caption is {len(caption)} characters, exceeding the configured maximum of {max_caption_length}"
        )

    if _MARKDOWN_FENCE_PATTERN.search(caption):
        raise DraftValidationFailedError("caption contains a markdown code fence")

    lowered = caption.lower()
    for marker in _PLACEHOLDER_PATTERNS:
        if marker in lowered:
            raise DraftValidationFailedError(f"caption contains placeholder/template text: {marker!r}")

    for banned in _BANNED_SUBSTRINGS:
        if banned in lowered:
            raise DraftValidationFailedError(f"caption exposes an internal/technical term: {banned!r}")

    for phrase in _GENERIC_PHRASES:
        if phrase in lowered:
            raise DraftValidationFailedError(f"caption uses a known generic agency phrase: {phrase!r}")

    for phrase in _STRATEGY_EXPLANATION_PHRASES:
        if phrase in lowered:
            raise DraftValidationFailedError("caption explains its own strategy instead of being finished copy")

    if not _shares_grounded_content(caption, grounding_text):
        raise DraftValidationFailedError(
            "caption does not reference anything from the provided context — draft appears generic"
        )

    _check_no_invented_numbers(caption, grounding_text=grounding_text)

    emoji_count = len(_EMOJI_PATTERN.findall(caption))
    if emoji_count > MAX_EMOJI_COUNT:
        raise DraftValidationFailedError(f"caption uses {emoji_count} emoji, exceeding the maximum of {MAX_EMOJI_COUNT}")

    if len(content.hashtags) > max_hashtags:
        raise DraftValidationFailedError(
            f"draft has {len(content.hashtags)} hashtags, exceeding the configured maximum of {max_hashtags}"
        )
    for hashtag in content.hashtags:
        if not _HASHTAG_PATTERN.match(hashtag):
            raise DraftValidationFailedError(f"malformed hashtag: {hashtag!r}")

    if content.cta:
        cta_lowered = content.cta.strip().lower()
        for phrase in _GENERIC_CTA_PHRASES:
            if phrase in cta_lowered:
                raise DraftValidationFailedError(f"cta uses a generic sales phrase not clearly supported by the plan: {phrase!r}")
        if content.cta.strip() and content.cta.strip().lower() in lowered:
            raise DraftValidationFailedError("cta is duplicated verbatim inside the caption")


def _shares_grounded_content(text: str, grounding_text: str) -> bool:
    """Heuristic groundedness check — see content_plan_validation.py's
    identical helper and its documented limitation (word-overlap, not a
    paraphrase-aware semantic judgment)."""

    grounding_words = {w for w in re.findall(r"[a-zA-Z]{4,}", grounding_text.lower()) if w not in _STOPWORDS}
    text_words = {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in _STOPWORDS}
    return bool(grounding_words & text_words)


def _check_no_invented_numbers(text: str, *, grounding_text: str) -> None:
    grounding_numbers = set(_NUMBER_PATTERN.findall(grounding_text))
    for number in _NUMBER_PATTERN.findall(text):
        if number not in grounding_numbers:
            raise DraftValidationFailedError(
                f"caption states a number ({number!r}) not present anywhere in the provided context — "
                "possible invented factual claim"
            )


# Dispatch table mirroring CONTENT_MODELS_BY_OUTPUT_TYPE — extend the day
# a second output type gains supports_generation = True.
VALIDATORS_BY_OUTPUT_TYPE = {
    "instagram_reel_caption": validate_instagram_reel_caption,
}
