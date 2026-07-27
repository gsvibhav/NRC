"""Defensive parsing of Claude's structured JSON response.

Structured outputs (output_config.format on client.py's request) already
constrain Claude's response to match RESPONSE_SCHEMA — but we still parse
and validate here rather than trusting that blindly. Never use eval(). A
malformed or schema-mismatched response raises an error rather than being
silently repaired or partially accepted — a failed analysis must never be
reported as successful.
"""

from __future__ import annotations

import json
import logging

from .errors import AnalysisResponseSchemaMismatchError, MalformedAnalysisResponseError
from .models import AnalysisResult
from .prompts import RESPONSE_SCHEMA

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = frozenset(RESPONSE_SCHEMA["required"])
_ARRAY_FIELDS = tuple(name for name in _REQUIRED_FIELDS if name != "summary")


def parse_analysis_response(raw_text: str) -> AnalysisResult:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedAnalysisResponseError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedAnalysisResponseError(f"expected a JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise AnalysisResponseSchemaMismatchError(f"missing required field(s): {sorted(missing)}")

    extra = data.keys() - _REQUIRED_FIELDS
    if extra:
        raise AnalysisResponseSchemaMismatchError(f"unexpected extra field(s): {sorted(extra)}")

    summary = data["summary"]
    if not isinstance(summary, str):
        raise AnalysisResponseSchemaMismatchError(f"invalid field: 'summary' (got {type(summary).__name__})")

    for name in _ARRAY_FIELDS:
        value = data[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise AnalysisResponseSchemaMismatchError(f"invalid field: {name!r} (got {value!r})")

    return AnalysisResult(summary=summary, **{name: data[name] for name in _ARRAY_FIELDS})
