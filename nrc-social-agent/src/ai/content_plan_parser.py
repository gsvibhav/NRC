"""Defensive parsing of Claude's structured content-plan JSON.

Same defense-in-depth philosophy as src/ai/parser.py and
clarification_parser.py: structured outputs already constrain the shape,
but this still parses and validates independently. Never eval(). A
malformed or schema-mismatched response raises rather than being silently
repaired or partially accepted. Business-rule validation (generic plans,
final-copy leakage, output-count limits, duplicate/unsupported types,
etc.) is a separate, later step — see content_plan_validation.py.
"""

from __future__ import annotations

import json

from .content_plan_models import ExcludedOutput, PlanStrategy, PlannedOutput
from .content_plan_prompts import CONTENT_PLAN_RESPONSE_SCHEMA
from .errors import ContentPlanSchemaMismatchError, MalformedContentPlanResponseError

_REQUIRED_FIELDS = frozenset(CONTENT_PLAN_RESPONSE_SCHEMA["required"])
_STRATEGY_REQUIRED_FIELDS = frozenset(CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["strategy"]["required"])
_OUTPUT_REQUIRED_FIELDS = frozenset(
    CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["outputs"]["items"]["required"]
)
_EXCLUDED_REQUIRED_FIELDS = frozenset(
    CONTENT_PLAN_RESPONSE_SCHEMA["properties"]["excluded_outputs"]["items"]["required"]
)


class ParsedContentPlan:
    """The parsed-but-not-yet-business-validated response: still uses
    domain dataclasses for strategy/outputs/excluded_outputs, but
    `output_id` hasn't been assigned yet (service-assigned, like
    clarification's question_id — see content_planning_service.py)."""

    __slots__ = ("strategy", "outputs", "excluded_outputs")

    def __init__(self, *, strategy: PlanStrategy, outputs: list, excluded_outputs: list) -> None:
        self.strategy = strategy
        self.outputs = outputs
        self.excluded_outputs = excluded_outputs


def parse_content_plan_response(raw_text: str) -> ParsedContentPlan:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise MalformedContentPlanResponseError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MalformedContentPlanResponseError(f"expected a JSON object, got {type(data).__name__}")

    missing = _REQUIRED_FIELDS - data.keys()
    if missing:
        raise ContentPlanSchemaMismatchError(f"missing required field(s): {sorted(missing)}")

    extra = data.keys() - _REQUIRED_FIELDS
    if extra:
        raise ContentPlanSchemaMismatchError(f"unexpected extra field(s): {sorted(extra)}")

    strategy_raw = data["strategy"]
    _check_object(strategy_raw, "strategy", _STRATEGY_REQUIRED_FIELDS)
    strategy = PlanStrategy.from_dict(strategy_raw)

    outputs_raw = data["outputs"]
    if not isinstance(outputs_raw, list):
        raise ContentPlanSchemaMismatchError(f"invalid 'outputs': {outputs_raw!r}")
    outputs = []
    for index, output_raw in enumerate(outputs_raw):
        _check_object(output_raw, f"outputs[{index}]", _OUTPUT_REQUIRED_FIELDS)
        # output_id is assigned later, once business validation passes.
        outputs.append(
            PlannedOutput.from_dict({**output_raw, "output_id": "pending"})
        )

    excluded_raw = data["excluded_outputs"]
    if not isinstance(excluded_raw, list):
        raise ContentPlanSchemaMismatchError(f"invalid 'excluded_outputs': {excluded_raw!r}")
    excluded_outputs = []
    for index, excluded_item in enumerate(excluded_raw):
        _check_object(excluded_item, f"excluded_outputs[{index}]", _EXCLUDED_REQUIRED_FIELDS)
        excluded_outputs.append(ExcludedOutput.from_dict(excluded_item))

    return ParsedContentPlan(strategy=strategy, outputs=outputs, excluded_outputs=excluded_outputs)


def _check_object(raw: object, label: str, required_fields: frozenset) -> None:
    if not isinstance(raw, dict):
        raise ContentPlanSchemaMismatchError(f"invalid '{label}': expected an object, got {raw!r}")
    missing = required_fields - raw.keys()
    if missing:
        raise ContentPlanSchemaMismatchError(f"'{label}' missing required field(s): {sorted(missing)}")
    extra = raw.keys() - required_fields
    if extra:
        raise ContentPlanSchemaMismatchError(f"'{label}' has unexpected field(s): {sorted(extra)}")
