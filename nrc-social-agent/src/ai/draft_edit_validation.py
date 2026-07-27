"""Business-rule validation for the Draft Editing Service (Milestone 7).

Two distinct kinds of check, run at two distinct points:

1. **Pre-flight instruction scan** (`detect_unsupported_platform_switch`) —
   runs on the raw user instruction *before* any Claude call, so an
   out-of-scope request ("turn this into a LinkedIn post") never reaches
   generation at all; the current draft is left completely untouched.

2. **Post-response content validation** (`validate_draft_edit`) — reuses
   draft_validation.py's existing content-quality rules unchanged (length,
   markdown fence, placeholder text, internal-terminology leakage, generic
   phrasing, groundedness, invented numbers, emoji/hashtag limits, CTA
   genericness/duplication — see that module for what these do and don't
   catch), then adds edit-specific instruction-alignment checks on top:
   deterministic, high-confidence checks for a handful of clearly-scoped
   instruction categories (remove hashtags, remove CTA, shorten, remove
   emoji, preserve an exact line) — never a general-purpose instruction-
   understanding engine, per the brief's explicit "do not build a large
   rule engine" instruction. An instruction outside these categories gets
   no extra deterministic check beyond the shared content-quality rules;
   correctness there is the prompt's job, not code's.
"""

from __future__ import annotations

import re

from .draft_models import InstagramReelCaptionContent
from .draft_validation import validate_instagram_reel_caption
from .errors import DraftEditValidationFailedError

# Channel names other than Instagram (this milestone's only supported
# review channel) — a mention alongside switch-style phrasing is treated
# as an out-of-scope platform-switch request. Deliberately a small,
# explicit denylist, not a parse of every possible phrasing.
_OTHER_CHANNEL_NAMES = ("linkedin", "threads", "twitter", " x post", "tiktok", "facebook", "youtube", "website", "blog")
_SWITCH_PHRASES = (
    "turn this into", "make this a", "make this into", "post this on", "convert this to",
    "convert it to", "change this to a", "change this into a", "repost this on", "publish this on",
)


def detect_unsupported_platform_switch(instruction: str) -> str | None:
    """Returns the mentioned channel name if `instruction` looks like a
    request to switch platform/output type, else None. Deliberately
    conservative (only fires when a switch-style phrase AND another
    channel's name both appear) — a passing mention of another platform
    with no switch phrasing (e.g. "shorter than what we'd use on
    LinkedIn") is not flagged, since that's just a style reference, not a
    request to change this review's own output."""

    lowered = instruction.lower()
    if not any(phrase in lowered for phrase in _SWITCH_PHRASES):
        return None
    for channel in _OTHER_CHANNEL_NAMES:
        if channel in lowered:
            return channel.strip()
    return None


_REMOVE_HASHTAGS_PATTERN = re.compile(r"remove.*hashtag|no hashtag|without hashtag|delete.*hashtag", re.IGNORECASE)
_REMOVE_CTA_PATTERN = re.compile(r"remove.*cta|remove.*call.to.action|no cta|without.*cta|delete.*cta", re.IGNORECASE)
_SHORTEN_PATTERN = re.compile(r"\bshort(er|en)\b|\btighten\b|\btrim\b|\bcut.*(down|length)\b", re.IGNORECASE)
_REMOVE_EMOJI_PATTERN = re.compile(r"remove.*emoji|no emoji|without emoji|delete.*emoji", re.IGNORECASE)
_PRESERVE_LINE_PATTERN = re.compile(r"(?:keep|preserve|retain)[^\"“]*[\"“]([^\"”]+)[\"”]", re.IGNORECASE)

_EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

# A "meaningfully shorter" revision must drop at least this fraction of
# the parent caption's length — small rewordings that happen to trim a
# few characters don't count as honoring an explicit "shorten" request.
_MIN_SHORTENING_FRACTION = 0.10


def validate_draft_edit(
    content: InstagramReelCaptionContent,
    *,
    parent_content: dict,
    instruction: str,
    max_caption_length: int,
    max_hashtags: int,
    grounding_text: str,
) -> None:
    """Raises DraftEditValidationFailedError with a specific reason on any
    rule violation. Returns None (silently) if the revision passes."""

    # Shared content-quality rules — identical bar to a fresh generation.
    validate_instagram_reel_caption(
        content, max_caption_length=max_caption_length, max_hashtags=max_hashtags, grounding_text=grounding_text
    )

    lowered_instruction = instruction.lower()

    if _REMOVE_HASHTAGS_PATTERN.search(lowered_instruction) and content.hashtags:
        raise DraftEditValidationFailedError(
            "instruction asked to remove hashtags, but the revised draft still has some"
        )

    if _REMOVE_CTA_PATTERN.search(lowered_instruction) and content.cta:
        raise DraftEditValidationFailedError(
            "instruction asked to remove the CTA, but the revised draft still has one"
        )

    if _REMOVE_EMOJI_PATTERN.search(lowered_instruction):
        emoji_count = len(_EMOJI_PATTERN.findall(content.caption))
        if emoji_count > 0:
            raise DraftEditValidationFailedError(
                f"instruction asked to remove emoji, but the revised draft still has {emoji_count}"
            )

    if _SHORTEN_PATTERN.search(lowered_instruction):
        parent_caption = parent_content.get("caption", "") or ""
        if parent_caption:
            max_allowed = len(parent_caption) * (1 - _MIN_SHORTENING_FRACTION)
            if len(content.caption) > max_allowed:
                raise DraftEditValidationFailedError(
                    "instruction asked to shorten the draft, but the revision isn't meaningfully shorter "
                    f"(parent={len(parent_caption)} chars, revised={len(content.caption)} chars)"
                )

    preserve_match = _PRESERVE_LINE_PATTERN.search(instruction)
    if preserve_match:
        preserved_line = preserve_match.group(1).strip()
        if preserved_line and preserved_line not in content.caption:
            raise DraftEditValidationFailedError(
                f"instruction asked to preserve a specific line, but it isn't present in the revision: "
                f"{preserved_line!r}"
            )
