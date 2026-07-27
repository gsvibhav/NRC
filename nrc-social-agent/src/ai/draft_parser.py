"""Defensive parsing of Claude's structured draft-generation JSON.

Same defense-in-depth philosophy as the other parsers in this pipeline:
structured outputs already constrain the shape, but this still parses and
validates independently. Never eval(). Never attempts to extract JSON from
surrounding prose or markdown fences — a response that isn't already bare,
valid JSON is rejected as malformed rather than salvaged, since silently
"fixing" a wrapped response is exactly the kind of leniency that could let
a hidden-reasoning preamble or a second variant slip through unnoticed.
"""

from __future__ import annotations

import json

from .draft_models import CONTENT_MODELS_BY_OUTPUT_TYPE
from .draft_prompts import DRAFT_RESPONSE_SCHEMAS
from .errors import DraftResponseSchemaMismatchError, MalformedDraftResponseError


def parse_draft_response(output_type: str, raw_text: str):
    """Returns the output-type-specific content dataclass instance (e.g.
    `InstagramReelCaptionContent`). Raises MalformedDraftResponseError for
    invalid JSON, DraftResponseSchemaMismatchError for anything
    structurally wrong (missing/extra/mistyped fields, or an
    unrecognized `output_type`)."""

    if output_type not in DRAFT_RESPONSE_SCHEMAS:
        raise DraftResponseSchemaMismatchError(f"no response schema registered for output_type={output_type!r}")

    schema = DRAFT_RESPONSE_SCHEMAS[output_type]
    content_model = CONTENT_MODELS_BY_OUTPUT_TYPE[output_type]

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedDraftResponseError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedDraftResponseError(f"expected a JSON object, got {type(data).__name__}")

    required_fields = frozenset(schema["required"])
    missing = required_fields - data.keys()
    if missing:
        raise DraftResponseSchemaMismatchError(f"missing required field(s): {sorted(missing)}")

    extra = data.keys() - required_fields
    if extra:
        raise DraftResponseSchemaMismatchError(f"unexpected extra field(s): {sorted(extra)}")

    return content_model.from_dict(data)
