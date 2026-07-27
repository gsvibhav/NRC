"""Quality and safety validation for a generated clarification question,
run before it is ever persisted or sent to Telegram (see
clarification_service.py). Structured output and prompt instructions
already push Claude toward good questions, but this is the enforced,
code-level backstop — a bad question is a controlled failure (one
regeneration attempt, then fail safely), never silently sent.
"""

from __future__ import annotations

import difflib
import re

from .errors import InvalidClarificationQuestionError

# Generous enough for a natural, sometimes two-clause question; short
# enough to keep the bot's tone concise per the brief's "concise, easy to
# answer conversationally" requirement.
MAX_QUESTION_LENGTH = 320

# Above this ratio (0-1, via difflib.SequenceMatcher on normalized text),
# two questions are treated as "materially identical."
_DUPLICATE_SIMILARITY_THRESHOLD = 0.82

_NUMBERED_LIST_PATTERN = re.compile(r"(?:^|\n)\s*[1-9][.)]\s+", re.MULTILINE)

# Internal/technical terms a natural question must never surface.
_BANNED_SUBSTRINGS = (
    "json", "schema", "confidence score", "workflow", "state:", "internal field",
    "waiting_for_user", "analyzing_media", "generating_content", "target_field",
    "question_id", "api", "claude", "anthropic", "llm", "model:",
)

# Literal internal category names (see clarification_models.py's
# _SCALAR_FIELDS) — a natural question may use the underlying *concept*
# (e.g. the word "tone") but never the snake_case identifier itself.
_FIELD_NAME_TOKENS = (
    "brand_name", "content_type", "message_focus", "call_to_action",
    "user_preferences", "factual_context", "context_sufficient",
    "context_updates", "remaining_uncertainties",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def validate_question_text(text: str, *, previous_questions: list) -> None:
    """Raises InvalidClarificationQuestionError with a specific reason if
    `text` fails any quality/safety check. Returns None (silently) if it
    passes every check."""

    stripped = text.strip()
    if not stripped:
        raise InvalidClarificationQuestionError("question is empty")

    if len(stripped) > MAX_QUESTION_LENGTH:
        raise InvalidClarificationQuestionError(
            f"question exceeds the {MAX_QUESTION_LENGTH}-character limit ({len(stripped)} chars)"
        )

    if stripped.count("?") > 1:
        raise InvalidClarificationQuestionError("question contains more than one primary request")

    if _NUMBERED_LIST_PATTERN.search(stripped):
        raise InvalidClarificationQuestionError("question reads as a numbered questionnaire, not one natural question")

    lowered = stripped.lower()
    for banned in _BANNED_SUBSTRINGS:
        if banned in lowered:
            raise InvalidClarificationQuestionError(f"question exposes an internal/technical term: {banned!r}")

    for token in _FIELD_NAME_TOKENS:
        if token in lowered:
            raise InvalidClarificationQuestionError(f"question contains an internal field name: {token!r}")

    normalized = _normalize(stripped)
    for previous in previous_questions:
        similarity = difflib.SequenceMatcher(None, normalized, _normalize(previous)).ratio()
        if similarity >= _DUPLICATE_SIMILARITY_THRESHOLD:
            raise InvalidClarificationQuestionError("question is materially identical to one already asked")
