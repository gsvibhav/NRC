"""Domain models for the Content Planning Engine (Milestone 5): the
controlled output-type registry, the content-type classification, and the
persisted content-plan document.

Strategy vs. writing, encoded in the shapes themselves: nothing here has a
field wide enough to hold finished copy — `PlannedOutput.message_focus`
and friends are short strategic directions (see content_plan_validation.py
for the length/shape checks that keep it that way), never a caption body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    ContentPlanSchemaMismatchError,
    ContentPlanVersionMismatchError,
    InvalidContentPlanStatusError,
)

CURRENT_DOCUMENT_VERSION = 1
SUPPORTED_DOCUMENT_VERSIONS = {1}

DEFAULT_GENERATION_STATUS = "NOT_STARTED"


class OutputType(str, Enum):
    """The controlled registry of output types the planner may propose.
    Claude may choose only from this set — it cannot invent a free-form
    output type (enforced in content_plan_validation.py, not merely
    requested by prompt). Extending this set is a deliberate code change,
    not something a prompt edit alone can do."""

    INSTAGRAM_REEL_CAPTION = "instagram_reel_caption"
    INSTAGRAM_FEED_CAPTION = "instagram_feed_caption"
    INSTAGRAM_CAROUSEL_PLAN = "instagram_carousel_plan"
    LINKEDIN_POST = "linkedin_post"
    THREADS_POST = "threads_post"
    WEBSITE_PORTFOLIO_ENTRY = "website_portfolio_entry"
    WEBSITE_CASE_STUDY_OUTLINE = "website_case_study_outline"


@dataclass(frozen=True)
class OutputTypeDefinition:
    """Registry metadata for one output type — capability status only,
    never credentials or publishing logic (those don't exist yet, see
    ROADMAP.md Phase 3+)."""

    output_type: OutputType
    display_name: str
    channel: str
    future_generator: str
    supports_planning: bool
    supports_generation: bool
    supports_publishing: bool


# `supports_publishing` is False for every entry — no publishing adapter
# exists yet (ROADMAP Phase 3+). `supports_generation` is True only for
# `instagram_reel_caption` as of Milestone 6 (see
# src/ai/draft_generation_service.py) — chosen deliberately, not as a
# default: it's the sole example used throughout the Milestone 5/6 briefs,
# Telegram is V1's only surface (so an Instagram-shaped caption is the
# most natural first "reviewable draft"), and PRODUCT.md's V1 vision is a
# single generated post, not a multi-platform batch. Every other type
# remains planning-only until a future milestone deliberately extends
# generation support to it — see README.md's "Supported generation output
# types" for the full rationale.
OUTPUT_TYPE_REGISTRY: dict[OutputType, OutputTypeDefinition] = {
    OutputType.INSTAGRAM_REEL_CAPTION: OutputTypeDefinition(
        output_type=OutputType.INSTAGRAM_REEL_CAPTION,
        # Milestone 11A: deliberately placement-neutral. The internal
        # output_type value (instagram_reel_caption) and this type's
        # generation/prompting are unchanged — only the reviewer/planning-
        # prompt-facing *label* changed, because the actual Meta placement
        # (Feed image vs. Reel) is resolved from the approved media's own
        # type at publication-preparation time (see
        # src/publication/models.py's resolve_placement()), never from
        # this display name or the output_type string itself. "Reel
        # caption" previously implied a placement this type does not, on
        # its own, guarantee — see the Milestone 11A planning report.
        display_name="Instagram caption",
        channel="Instagram",
        future_generator="Instagram Reel caption generator (src/ai/draft_generation_service.py, since Milestone 6)",
        supports_planning=True, supports_generation=True, supports_publishing=False,
    ),
    OutputType.INSTAGRAM_FEED_CAPTION: OutputTypeDefinition(
        output_type=OutputType.INSTAGRAM_FEED_CAPTION,
        display_name="Instagram feed caption",
        channel="Instagram",
        future_generator="Instagram feed caption generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
    OutputType.INSTAGRAM_CAROUSEL_PLAN: OutputTypeDefinition(
        output_type=OutputType.INSTAGRAM_CAROUSEL_PLAN,
        display_name="Instagram carousel plan",
        channel="Instagram",
        future_generator="Instagram carousel generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
    OutputType.LINKEDIN_POST: OutputTypeDefinition(
        output_type=OutputType.LINKEDIN_POST,
        display_name="LinkedIn post",
        channel="LinkedIn",
        future_generator="LinkedIn post generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
    OutputType.THREADS_POST: OutputTypeDefinition(
        output_type=OutputType.THREADS_POST,
        display_name="Threads post",
        channel="Threads",
        future_generator="Threads post generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
    OutputType.WEBSITE_PORTFOLIO_ENTRY: OutputTypeDefinition(
        output_type=OutputType.WEBSITE_PORTFOLIO_ENTRY,
        display_name="Website portfolio entry",
        channel="Website",
        future_generator="Website portfolio entry generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
    OutputType.WEBSITE_CASE_STUDY_OUTLINE: OutputTypeDefinition(
        output_type=OutputType.WEBSITE_CASE_STUDY_OUTLINE,
        display_name="Website case-study outline",
        channel="Website",
        future_generator="Website case-study generator (not yet implemented)",
        supports_planning=True, supports_generation=False, supports_publishing=False,
    ),
}


class ContentType(str, Enum):
    WORK_SHOWCASE = "work_showcase"
    CASE_STUDY = "case_study"
    CAMPAIGN = "campaign"
    BEHIND_THE_SCENES = "behind_the_scenes"
    FOUNDER_INTRODUCTION = "founder_introduction"
    BRAND_ANNOUNCEMENT = "brand_announcement"
    SERVICE_INTRODUCTION = "service_introduction"
    EDUCATIONAL_CONTENT = "educational_content"
    CLIENT_RESULT = "client_result"
    EVENT = "event"
    TESTIMONIAL = "testimonial"
    GENERAL_BRAND_CONTENT = "general_brand_content"
    OTHER = "other"


class ContentPlanStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


_STRATEGY_REQUIRED_STRING_FIELDS = ("primary_objective", "central_message", "brand_positioning", "cta_direction")
_STRATEGY_REQUIRED_LIST_FIELDS = ("audience", "tone_direction", "factual_constraints", "avoid")


@dataclass(frozen=True)
class PlanStrategy:
    """The single strategic understanding shared across every proposed
    output — never itself finished copy, just direction. See
    content_plan_validation.py for what keeps it that way."""

    content_type: str
    primary_objective: str
    central_message: str
    brand_positioning: str
    cta_direction: str
    audience: list = field(default_factory=list)
    tone_direction: list = field(default_factory=list)
    factual_constraints: list = field(default_factory=list)
    avoid: list = field(default_factory=list)
    content_type_description: str | None = None
    supporting_objective: str | None = None

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "content_type_description": self.content_type_description,
            "primary_objective": self.primary_objective,
            "supporting_objective": self.supporting_objective,
            "audience": self.audience,
            "central_message": self.central_message,
            "brand_positioning": self.brand_positioning,
            "tone_direction": self.tone_direction,
            "cta_direction": self.cta_direction,
            "factual_constraints": self.factual_constraints,
            "avoid": self.avoid,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStrategy":
        if not isinstance(data, dict):
            raise ContentPlanSchemaMismatchError(f"expected an object for 'strategy', got {data!r}")

        for name in _STRATEGY_REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise ContentPlanSchemaMismatchError(f"invalid field: 'strategy.{name}' (got {value!r})")

        for name in _STRATEGY_REQUIRED_LIST_FIELDS:
            value = data.get(name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ContentPlanSchemaMismatchError(f"invalid field: 'strategy.{name}' (got {value!r})")

        content_type_description = data.get("content_type_description")
        if content_type_description is not None and not isinstance(content_type_description, str):
            raise ContentPlanSchemaMismatchError("invalid field: 'strategy.content_type_description'")

        supporting_objective = data.get("supporting_objective")
        if supporting_objective is not None and not isinstance(supporting_objective, str):
            raise ContentPlanSchemaMismatchError("invalid field: 'strategy.supporting_objective'")

        return cls(
            content_type=data["content_type"],
            content_type_description=content_type_description or None,
            primary_objective=data["primary_objective"],
            supporting_objective=supporting_objective or None,
            audience=data["audience"],
            central_message=data["central_message"],
            brand_positioning=data["brand_positioning"],
            tone_direction=data["tone_direction"],
            cta_direction=data["cta_direction"],
            factual_constraints=data["factual_constraints"],
            avoid=data["avoid"],
        )


_OUTPUT_REQUIRED_STRING_FIELDS = ("output_id", "output_type", "purpose", "message_focus", "cta_direction")
_OUTPUT_REQUIRED_LIST_FIELDS = ("audience", "tone", "required_context", "constraints")


@dataclass(frozen=True)
class PlannedOutput:
    output_id: str
    output_type: str
    priority: int
    purpose: str
    message_focus: str
    cta_direction: str
    audience: list = field(default_factory=list)
    tone: list = field(default_factory=list)
    required_context: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    generation_status: str = DEFAULT_GENERATION_STATUS

    def to_dict(self) -> dict:
        return {
            "output_id": self.output_id,
            "output_type": self.output_type,
            "priority": self.priority,
            "purpose": self.purpose,
            "audience": self.audience,
            "message_focus": self.message_focus,
            "tone": self.tone,
            "cta_direction": self.cta_direction,
            "required_context": self.required_context,
            "constraints": self.constraints,
            "generation_status": self.generation_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlannedOutput":
        if not isinstance(data, dict):
            raise ContentPlanSchemaMismatchError(f"expected an object for an output, got {data!r}")

        for name in _OUTPUT_REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise ContentPlanSchemaMismatchError(f"invalid field: 'outputs[].{name}' (got {value!r})")

        for name in _OUTPUT_REQUIRED_LIST_FIELDS:
            value = data.get(name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ContentPlanSchemaMismatchError(f"invalid field: 'outputs[].{name}' (got {value!r})")

        priority = data.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'outputs[].priority' (got {priority!r})")

        generation_status = data.get("generation_status", DEFAULT_GENERATION_STATUS)
        if not isinstance(generation_status, str) or not generation_status:
            raise ContentPlanSchemaMismatchError("invalid field: 'outputs[].generation_status'")

        return cls(
            output_id=data["output_id"],
            output_type=data["output_type"],
            priority=priority,
            purpose=data["purpose"],
            audience=data["audience"],
            message_focus=data["message_focus"],
            tone=data["tone"],
            cta_direction=data["cta_direction"],
            required_context=data["required_context"],
            constraints=data["constraints"],
            generation_status=generation_status,
        )


@dataclass(frozen=True)
class ExcludedOutput:
    output_type: str
    reason: str

    def to_dict(self) -> dict:
        return {"output_type": self.output_type, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict) -> "ExcludedOutput":
        if not isinstance(data, dict):
            raise ContentPlanSchemaMismatchError(f"expected an object for an excluded output, got {data!r}")
        output_type = data.get("output_type")
        reason = data.get("reason")
        if not isinstance(output_type, str) or not output_type:
            raise ContentPlanSchemaMismatchError(f"invalid field: 'excluded_outputs[].output_type' (got {output_type!r})")
        if not isinstance(reason, str) or not reason:
            raise ContentPlanSchemaMismatchError(f"invalid field: 'excluded_outputs[].reason' (got {reason!r})")
        return cls(output_type=output_type, reason=reason)


@dataclass(frozen=True)
class ContentPlanUsage:
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    @classmethod
    def from_dict(cls, data: dict) -> "ContentPlanUsage":
        input_tokens = data.get("input_tokens")
        output_tokens = data.get("output_tokens")
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'usage.input_tokens' (got {input_tokens!r})")
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'usage.output_tokens' (got {output_tokens!r})")
        return cls(input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True)
class ContentPlanDocument:
    plan_id: str
    workflow_id: str
    created_at: str
    updated_at: str
    status: ContentPlanStatus
    schema_version: int
    prompt_version: int
    model: str
    strategy: PlanStrategy | None = None
    outputs: list = field(default_factory=list)
    excluded_outputs: list = field(default_factory=list)
    usage: ContentPlanUsage | None = None
    metadata: dict = field(default_factory=dict)
    version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "plan_id": self.plan_id,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "strategy": self.strategy.to_dict() if self.strategy is not None else None,
            "outputs": [o.to_dict() for o in self.outputs],
            "excluded_outputs": [e.to_dict() for e in self.excluded_outputs],
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContentPlanDocument":
        if not isinstance(data, dict):
            raise ContentPlanSchemaMismatchError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise ContentPlanVersionMismatchError(
                f"unsupported content plan document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        required_strings = ("plan_id", "workflow_id", "created_at", "updated_at", "model")
        for name in required_strings:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise ContentPlanSchemaMismatchError(f"missing or invalid field: {name!r}")

        status_raw = data.get("status")
        try:
            status = ContentPlanStatus(status_raw)
        except ValueError as exc:
            raise InvalidContentPlanStatusError(f"unrecognized status: {status_raw!r}") from exc

        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'schema_version' (got {schema_version!r})")

        prompt_version = data.get("prompt_version")
        if not isinstance(prompt_version, int) or isinstance(prompt_version, bool):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'prompt_version' (got {prompt_version!r})")

        strategy_raw = data.get("strategy")
        strategy = PlanStrategy.from_dict(strategy_raw) if strategy_raw is not None else None

        outputs_raw = data.get("outputs", [])
        if not isinstance(outputs_raw, list):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'outputs' (got {outputs_raw!r})")
        outputs = [PlannedOutput.from_dict(o) for o in outputs_raw]

        output_ids = [o.output_id for o in outputs]
        if len(output_ids) != len(set(output_ids)):
            raise ContentPlanSchemaMismatchError(f"duplicate 'output_id' among outputs: {output_ids!r}")

        excluded_raw = data.get("excluded_outputs", [])
        if not isinstance(excluded_raw, list):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'excluded_outputs' (got {excluded_raw!r})")
        excluded_outputs = [ExcludedOutput.from_dict(e) for e in excluded_raw]

        usage_raw = data.get("usage")
        usage = ContentPlanUsage.from_dict(usage_raw) if usage_raw is not None else None

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise ContentPlanSchemaMismatchError(f"invalid field: 'metadata' (got {metadata!r})")

        return cls(
            plan_id=data["plan_id"],
            workflow_id=data["workflow_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=data["model"],
            strategy=strategy,
            outputs=outputs,
            excluded_outputs=excluded_outputs,
            usage=usage,
            metadata=metadata,
            version=version,
        )
