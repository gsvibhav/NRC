"""Defensive parsing of Claude's structured clarification-decision JSON.

Same defense-in-depth philosophy as src/ai/parser.py: structured outputs
(output_config.format on client.py's request) already constrain the shape,
but this still parses and validates independently rather than trusting
that blindly. Never eval(). A malformed or schema-mismatched response
raises rather than being silently repaired or partially accepted.
"""

from __future__ import annotations

import json

from .clarification_models import ClarificationDecision, ClarificationDecisionType, ClarificationQuestion
from .clarification_prompts import CLARIFICATION_RESPONSE_SCHEMA
from .errors import ClarificationResponseSchemaMismatchError, MalformedClarificationResponseError

_REQUIRED_FIELDS = frozenset(CLARIFICATION_RESPONSE_SCHEMA["required"])
_CONTEXT_UPDATE_FIELDS = frozenset(CLARIFICATION_RESPONSE_SCHEMA["properties"]["context_updates"]["required"])
_CONTEXT_UPDATE_LIST_FIELDS = frozenset({"platforms"})
_CONTEXT_UPDATE_DICT_FIELDS = frozenset({"factual_context", "user_preferences"})
_CONTEXT_UPDATE_STRING_FIELDS = _CONTEXT_UPDATE_FIELDS - _CONTEXT_UPDATE_LIST_FIELDS - _CONTEXT_UPDATE_DICT_FIELDS

_ALLOWED_TARGET_FIELDS = frozenset(
    {
        "brand_name", "content_type", "objective", "audience", "message_focus",
        "tone", "call_to_action", "platforms", "factual_context", "other", "",
    }
)


def parse_clarification_response(raw_text: str) -> ClarificationDecision:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedClarificationResponseError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedClarificationResponseError(f"expected a JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ClarificationResponseSchemaMismatchError(f"missing required field(s): {sorted(missing)}")

    extra = data.keys() - _REQUIRED_FIELDS
    if extra:
        raise ClarificationResponseSchemaMismatchError(f"unexpected extra field(s): {sorted(extra)}")

    decision_raw = data["decision"]
    try:
        decision_type = ClarificationDecisionType(decision_raw)
    except ValueError as exc:
        raise ClarificationResponseSchemaMismatchError(f"invalid 'decision': {decision_raw!r}") from exc

    context_sufficient = data["context_sufficient"]
    if not isinstance(context_sufficient, bool):
        raise ClarificationResponseSchemaMismatchError(f"invalid 'context_sufficient': {context_sufficient!r}")

    for name in ("question_text", "question_purpose", "question_target_field"):
        if not isinstance(data[name], str):
            raise ClarificationResponseSchemaMismatchError(f"invalid field: {name!r} (got {data[name]!r})")

    if data["question_target_field"] not in _ALLOWED_TARGET_FIELDS:
        raise ClarificationResponseSchemaMismatchError(
            f"invalid 'question_target_field': {data['question_target_field']!r}"
        )

    confidence = data["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not (0.0 <= confidence <= 1.0):
        raise ClarificationResponseSchemaMismatchError(f"invalid 'confidence': {confidence!r}")

    remaining_uncertainties = data["remaining_uncertainties"]
    if not isinstance(remaining_uncertainties, list) or not all(isinstance(u, str) for u in remaining_uncertainties):
        raise ClarificationResponseSchemaMismatchError(f"invalid 'remaining_uncertainties': {remaining_uncertainties!r}")

    context_updates = _parse_context_updates(data["context_updates"])

    if decision_type is ClarificationDecisionType.ASK_QUESTION:
        question_text = data["question_text"].strip()
        if not question_text:
            raise ClarificationResponseSchemaMismatchError("ASK_QUESTION requires a non-empty 'question_text'")
        question = ClarificationQuestion(
            text=question_text,
            purpose=data["question_purpose"].strip(),
            target_field=data["question_target_field"],
        )
    else:
        question = None

    return ClarificationDecision(
        decision=decision_type,
        context_sufficient=context_sufficient,
        question=question,
        context_updates=context_updates,
        remaining_uncertainties=remaining_uncertainties,
        confidence=float(confidence),
    )


def _parse_context_updates(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ClarificationResponseSchemaMismatchError(f"invalid 'context_updates': {raw!r}")

    missing = _CONTEXT_UPDATE_FIELDS - raw.keys()
    if missing:
        raise ClarificationResponseSchemaMismatchError(f"context_updates missing field(s): {sorted(missing)}")

    extra = raw.keys() - _CONTEXT_UPDATE_FIELDS
    if extra:
        raise ClarificationResponseSchemaMismatchError(f"context_updates has unexpected field(s): {sorted(extra)}")

    for name in _CONTEXT_UPDATE_STRING_FIELDS:
        if not isinstance(raw[name], str):
            raise ClarificationResponseSchemaMismatchError(f"invalid context_updates.{name}: {raw[name]!r}")

    for name in _CONTEXT_UPDATE_LIST_FIELDS:
        value = raw[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ClarificationResponseSchemaMismatchError(f"invalid context_updates.{name}: {value!r}")

    for name in _CONTEXT_UPDATE_DICT_FIELDS:
        value = raw[name]
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise ClarificationResponseSchemaMismatchError(f"invalid context_updates.{name}: {value!r}")

    return raw
