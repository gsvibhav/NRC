"""Business-rule validation for a freshly-parsed content plan, run before
it is ever persisted (see content_planning_service.py). Structured output
and prompt instructions already push Claude toward a good plan, but this
is the enforced, code-level backstop — a plan failing any rule here is a
controlled failure (one regeneration attempt, then fail safely), never
silently repaired or persisted as valid.

Two families of rule:
1. **Structural/registry rules** — output count, output types, priorities,
   proposed/excluded conflicts. These are exact and reliable.
2. **Content-quality heuristics** — final-copy leakage, generic-plan
   detection, invented-specificity detection. These are necessarily
   imperfect pattern checks, not semantic judgment (no second Claude call
   is used to grade the first one) — documented as a known limitation in
   README.md, not silently claimed to be exhaustive.
"""

from __future__ import annotations

import re

from .content_plan_models import ContentType, OUTPUT_TYPE_REGISTRY
from .content_plan_parser import ParsedContentPlan
from .errors import ContentPlanValidationFailedError

# A completed strategic direction, not a caption — real captions/posts run
# noticeably longer than this in practice. A deliberately generous ceiling
# so genuinely concise strategy never trips it, while an actual paragraph
# of copy (see the brief's "already copywriting" example) does.
MAX_STRATEGY_FIELD_LENGTH = 220

_KNOWN_CONTENT_TYPES = {c.value for c in ContentType}
_KNOWN_OUTPUT_TYPES = set(OUTPUT_TYPE_REGISTRY.keys())
_KNOWN_OUTPUT_TYPE_VALUES = {t.value for t in _KNOWN_OUTPUT_TYPES}

# Milestone 11A: the authoring-contract invariant — "the planning layer
# must never select an output type that the current generation layer
# cannot generate." Registered-but-not-yet-generation-supported types
# (e.g. instagram_feed_caption) remain fully valid to *propose* — as a
# priority 2/3 supporting/optional idea, which is never auto-generated
# (see src/ai/draft_generation_service.py's `_select_primary_output()`,
# which only ever acts on priority 1) — they simply cannot become the
# priority-1 output while generation support is absent. This is enforced
# here, at plan-validation time, specifically so an unsupported selection
# is a normal, one-retry-then-informative-failure planning outcome, never
# a downstream generation-stage crash discovered only after the plan was
# already persisted and shown to the user.
_GENERATION_SUPPORTED_OUTPUT_TYPE_VALUES = {
    t.value for t, definition in OUTPUT_TYPE_REGISTRY.items() if definition.supports_generation
}

_VALID_PRIORITIES = {1, 2, 3}

# Internal/technical terms a strategy statement must never surface —
# mirrors question_validation.py's equivalent list.
_BANNED_SUBSTRINGS = (
    "json", "schema", "s3", "workflow_id", "api key", "anthropic", "claude",
    "token", "internal field", "database", "bucket",
)

# Patterns suggesting actual finished copy rather than a strategic
# direction: hashtags, an em-dash-led hook, or a quoted line (a common way
# a caption gets embedded inside a "purpose" field).
_HASHTAG_PATTERN = re.compile(r"#\w")
_MULTI_SENTENCE_PATTERN = re.compile(r"[.!?]\s+[A-Z].*[.!?]\s+[A-Z]")  # 3+ sentences

# The brief's own cited examples of weak, ungrounded strategy — an exact
# (case-insensitive) match on a core strategic field is a strong signal
# the plan was not actually grounded in this asset.
_GENERIC_PHRASES = {
    "increase engagement", "social media users", "showcase the brand",
    "learn more", "professional",
}

_STOPWORDS = frozenset(
    "a an the of and or to in on for with is are this that from as by "
    "your our their its it be will can into about across than then over "
    "under out up down at not no yes just also more most very".split()
)

_NUMBER_PATTERN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


def validate_content_plan(parsed: ParsedContentPlan, *, max_outputs: int, grounding_text: str) -> None:
    """Raises ContentPlanValidationFailedError with a specific reason on
    any rule violation. Returns None (silently) if the plan passes."""

    _validate_strategy(parsed.strategy, grounding_text=grounding_text)
    _validate_output_count(parsed.outputs, max_outputs=max_outputs)
    _validate_output_types_and_priorities(parsed.outputs)
    _validate_output_fields(parsed.outputs, grounding_text=grounding_text)
    _validate_excluded_outputs(parsed.excluded_outputs, proposed_types={o.output_type for o in parsed.outputs})


def _validate_strategy(strategy, *, grounding_text: str) -> None:
    if strategy.content_type not in _KNOWN_CONTENT_TYPES:
        raise ContentPlanValidationFailedError(f"unsupported content_type: {strategy.content_type!r}")

    for field_name in ("primary_objective", "central_message", "brand_positioning", "cta_direction"):
        _check_not_final_copy(getattr(strategy, field_name), field_name)
        _check_no_banned_terms(getattr(strategy, field_name), field_name)

    if strategy.central_message.strip().lower() in _GENERIC_PHRASES:
        raise ContentPlanValidationFailedError("strategy.central_message is a known generic phrase, not grounded")
    if strategy.primary_objective.strip().lower() in _GENERIC_PHRASES:
        raise ContentPlanValidationFailedError("strategy.primary_objective is a known generic phrase, not grounded")

    if not _shares_grounded_content(strategy.central_message, grounding_text):
        raise ContentPlanValidationFailedError(
            "strategy.central_message does not reference anything from the provided context — plan appears generic"
        )

    _check_no_invented_numbers(strategy.factual_constraints, grounding_text=grounding_text, field_name="strategy.factual_constraints")
    _check_no_invented_numbers([strategy.central_message, strategy.brand_positioning], grounding_text=grounding_text, field_name="strategy")


def _validate_output_count(outputs: list, *, max_outputs: int) -> None:
    if len(outputs) == 0:
        raise ContentPlanValidationFailedError("plan proposes zero outputs — at least one is required")
    if len(outputs) > max_outputs:
        raise ContentPlanValidationFailedError(
            f"plan proposes {len(outputs)} outputs, exceeding the configured maximum of {max_outputs}"
        )


def _validate_output_types_and_priorities(outputs: list) -> None:
    output_types = [o.output_type for o in outputs]
    for output_type in output_types:
        if output_type not in _KNOWN_OUTPUT_TYPE_VALUES:
            raise ContentPlanValidationFailedError(f"unsupported output_type: {output_type!r}")

    if len(output_types) != len(set(output_types)):
        raise ContentPlanValidationFailedError(
            f"plan proposes duplicate output types (no distinct-variant mechanism exists yet): {output_types!r}"
        )

    priorities = [o.priority for o in outputs]
    for priority in priorities:
        if priority not in _VALID_PRIORITIES:
            raise ContentPlanValidationFailedError(f"invalid priority: {priority!r} (must be one of {sorted(_VALID_PRIORITIES)})")

    primary_count = sum(1 for p in priorities if p == 1)
    if primary_count != 1:
        raise ContentPlanValidationFailedError(
            f"exactly one output must be priority 1 (primary recommendation); found {primary_count}"
        )

    primary_output = next(o for o in outputs if o.priority == 1)
    if primary_output.output_type not in _GENERATION_SUPPORTED_OUTPUT_TYPE_VALUES:
        raise ContentPlanValidationFailedError(
            f"output_type {primary_output.output_type!r} does not support generation yet and cannot be the "
            f"priority-1 output — choose a generation-supported output_type "
            f"({sorted(_GENERATION_SUPPORTED_OUTPUT_TYPE_VALUES)}) as the primary recommendation, or propose "
            f"{primary_output.output_type!r} at a lower priority instead"
        )


def _validate_output_fields(outputs: list, *, grounding_text: str) -> None:
    message_focuses = []
    for output in outputs:
        for field_name in ("purpose", "message_focus", "cta_direction"):
            value = getattr(output, field_name)
            _check_not_final_copy(value, f"outputs[{output.output_type}].{field_name}")
            _check_no_banned_terms(value, f"outputs[{output.output_type}].{field_name}")

        if len(output.purpose.strip()) < 8:
            raise ContentPlanValidationFailedError(
                f"outputs[{output.output_type}].purpose is too short to be a meaningful strategic role"
            )
        if len(output.message_focus.strip()) < 8:
            raise ContentPlanValidationFailedError(
                f"outputs[{output.output_type}].message_focus is too short to be meaningful"
            )
        message_focuses.append(output.message_focus.strip().lower())

    if len(outputs) > 1 and len(set(message_focuses)) != len(message_focuses):
        raise ContentPlanValidationFailedError(
            "multiple outputs share an identical message_focus — outputs must have a distinct role"
        )


def _validate_excluded_outputs(excluded_outputs: list, *, proposed_types: set) -> None:
    for excluded in excluded_outputs:
        if excluded.output_type not in _KNOWN_OUTPUT_TYPE_VALUES:
            raise ContentPlanValidationFailedError(f"excluded output uses an unsupported output_type: {excluded.output_type!r}")
        if excluded.output_type in proposed_types:
            raise ContentPlanValidationFailedError(
                f"output_type {excluded.output_type!r} is both proposed and excluded — conflicting plan"
            )
        _check_no_banned_terms(excluded.reason, f"excluded_outputs[{excluded.output_type}].reason")


def _check_not_final_copy(text: str, field_name: str) -> None:
    if len(text) > MAX_STRATEGY_FIELD_LENGTH:
        raise ContentPlanValidationFailedError(
            f"{field_name} is {len(text)} characters — reads like finished copy, not a strategic direction "
            f"(limit {MAX_STRATEGY_FIELD_LENGTH})"
        )
    if _HASHTAG_PATTERN.search(text):
        raise ContentPlanValidationFailedError(f"{field_name} contains a hashtag — final-copy leakage")
    if '"' in text or "“" in text:
        raise ContentPlanValidationFailedError(f"{field_name} contains a quoted line — likely embedded finished copy")
    if _MULTI_SENTENCE_PATTERN.search(text):
        raise ContentPlanValidationFailedError(f"{field_name} reads as multi-sentence prose — likely finished copy")


def _check_no_banned_terms(text: str, field_name: str) -> None:
    lowered = text.lower()
    for banned in _BANNED_SUBSTRINGS:
        if banned in lowered:
            raise ContentPlanValidationFailedError(f"{field_name} exposes an internal/technical term: {banned!r}")


def _shares_grounded_content(text: str, grounding_text: str) -> bool:
    """Heuristic groundedness check: does `text` share at least one
    non-trivial word with the context actually provided? A documented,
    imperfect proxy for "is this specific to the asset" — see module
    docstring."""

    grounding_words = {w for w in re.findall(r"[a-zA-Z]{4,}", grounding_text.lower()) if w not in _STOPWORDS}
    text_words = {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in _STOPWORDS}
    return bool(grounding_words & text_words)


def _check_no_invented_numbers(texts: list, *, grounding_text: str, field_name: str) -> None:
    """Reject a strategic field that states a specific number (a result,
    count, date, or percentage) that doesn't appear anywhere in the
    provided context — a cheap, honest proxy for "don't invent facts."
    Only catches invented *numbers*, not invented qualitative claims — see
    README.md's documented limitation."""

    grounding_numbers = set(_NUMBER_PATTERN.findall(grounding_text))
    for text in texts:
        for number in _NUMBER_PATTERN.findall(text):
            if number not in grounding_numbers:
                raise ContentPlanValidationFailedError(
                    f"{field_name} states a number ({number!r}) not present anywhere in the provided context — "
                    "possible invented factual claim"
                )
