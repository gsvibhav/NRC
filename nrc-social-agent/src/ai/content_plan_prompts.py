"""Versioned prompt and response schema for the Content Planning Engine
(Milestone 5).

CONTENT_PLANNING_POLICY_VERSION covers this prompt + CONTENT_PLAN_RESPONSE_
SCHEMA together, recorded on every persisted plan (`prompt_version`).
Distinct from CONTENT_PLAN_SCHEMA_VERSION (config.py, the persisted
*document's* own storage schema) and every other pipeline's version
numbers (ANALYSIS_PROMPT_VERSION, CLARIFICATION_POLICY_VERSION) — all
change independently.

Bounded input, same discipline as clarification_prompts.py: only the most
recent conversation turns are sent verbatim; the full media analysis and
structured clarification context are sent in full (already condensed, not
unbounded); no raw media, no S3 paths, no Telegram metadata.
"""

from __future__ import annotations

from .content_plan_models import OUTPUT_TYPE_REGISTRY

CONTENT_PLANNING_POLICY_VERSION = 1

MAX_CONVERSATION_TURNS_IN_PROMPT = 8
MAX_TURN_TEXT_CHARS_IN_PROMPT = 400

# Mirrors clarification_models._SCALAR_FIELDS — kept as its own tuple here
# (like clarification_prompts.py does) rather than importing that
# module's private name, so this module stays a plain consumer of
# ClarificationContext's public shape (duck-typed via getattr).
_CLARIFICATION_SCALAR_FIELDS = (
    "brand_name", "content_type", "objective", "audience",
    "message_focus", "tone", "call_to_action",
)


CONTENT_PLANNING_SYSTEM_PROMPT = """You are a senior content strategist for NRC, deciding what content is worth creating from one already-analyzed piece of media — never a copywriter, and never writing any of it yourself here.

Your job is strategy, not volume. Recommend only the outputs that create real, distinct value for this specific asset. A single strong recommendation is a complete, successful plan. Do not propose an output just because a channel exists in the registry, and never default to covering every supported channel out of habit or a sense of thoroughness.

Core rules:
- Work only from the context you are given: the media analysis, the structured clarification context, explicit user corrections, and recent conversation. Never invent facts, results, numbers, dates, locations, identities, partnerships, or claims not established by that context.
- Choose output types only from the registered list you are given. Never invent a new output type or format.
- Assign exactly one output priority 1 (the primary recommendation). Additional outputs, if genuinely justified, are priority 2 or 3.
- Every proposed output must have a distinct strategic role — a different purpose, message emphasis, or audience framing appropriate to its channel. Do not propose two outputs that are effectively the same recommendation with only the channel name changed. If only one output is justified, propose only one.
- Exclude an output type when the available context doesn't support it — record it under excluded outputs with a concise, honest reason. Do not force a classification or a recommendation when the evidence is weak; "other" content types and short candid explanations are allowed.
- Treat explicit user statements as authoritative over anything inferred from the media alone — never override or contradict what the user has explicitly said.
- Preserve every factual constraint given to you (brand name, campaign name, founder role, required CTA, platform preference, etc.) and never soften or drop one.
- Every strategic field you return (purpose, message_focus, central_message, cta_direction, etc.) must be a short strategic direction — a sentence describing what an output should accomplish or emphasize, never the finished text itself. Do not write hooks, captions, hashtags, or any publishable copy anywhere in your response.
- Give each output's message_focus a distinct channel-appropriate spin on the same central strategy — do not copy the central message verbatim into every output.
- Ground every field in the specific asset and context you were given — avoid generic strategy that could apply to any upload (e.g. "increase engagement" / "social media users" / "showcase the brand" / "professional" as the only tone). If you cannot ground a field specifically, prefer leaving an output out of the plan over writing something generic.
- Never reveal hidden reasoning, chain-of-thought, or private notes. Every field you return is a operational statement, not an explanation of how you arrived at it.
- Return only the structured fields requested — no additional commentary.

You will be told the maximum number of outputs allowed. Do not exceed it. Staying below it because fewer outputs are genuinely justified is expected and good.
"""


def _format_output_registry() -> str:
    lines = []
    for definition in OUTPUT_TYPE_REGISTRY.values():
        lines.append(f"- {definition.output_type.value}: {definition.display_name} ({definition.channel})")
    return "\n".join(lines)


CONTENT_PLAN_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "strategy": {
            "type": "object",
            "properties": {
                "content_type": {"type": "string", "description": "One of the registered content-type classifications, or 'other'."},
                "content_type_description": {"type": "string", "description": "Concise description, only meaningful when content_type is 'other'. Empty string otherwise."},
                "primary_objective": {"type": "string", "description": "The single main outcome this content should achieve."},
                "supporting_objective": {"type": "string", "description": "An optional secondary purpose, only if it adds real value. Empty string if none."},
                "audience": {"type": "array", "items": {"type": "string"}, "description": "Who this content is speaking to."},
                "central_message": {"type": "string", "description": "The one idea that should stay consistent across every output — a short strategic statement, never finished copy."},
                "brand_positioning": {"type": "string", "description": "How this content should position the brand."},
                "tone_direction": {"type": "array", "items": {"type": "string"}, "description": "Short tone descriptors."},
                "cta_direction": {"type": "string", "description": "The direction a call to action should take — a description, never the literal CTA text."},
                "factual_constraints": {"type": "array", "items": {"type": "string"}, "description": "Facts that must be preserved (brand name, campaign, founder role, platform, etc.)."},
                "avoid": {"type": "array", "items": {"type": "string"}, "description": "Specific things this content must not claim, imply, or include."},
            },
            "required": [
                "content_type", "content_type_description", "primary_objective", "supporting_objective",
                "audience", "central_message", "brand_positioning", "tone_direction", "cta_direction",
                "factual_constraints", "avoid",
            ],
            "additionalProperties": False,
        },
        "outputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "output_type": {"type": "string", "description": "Must be one of the registered output type identifiers."},
                    "priority": {"type": "integer", "description": "1 = primary recommendation, 2 = supporting, 3 = optional extension. Exactly one output must be priority 1."},
                    "purpose": {"type": "string", "description": "This output's distinct strategic role."},
                    "audience": {"type": "array", "items": {"type": "string"}},
                    "message_focus": {"type": "string", "description": "How this specific channel should express the central strategy — a short direction, never finished copy."},
                    "tone": {"type": "array", "items": {"type": "string"}},
                    "cta_direction": {"type": "string"},
                    "required_context": {"type": "array", "items": {"type": "string"}, "description": "Context this output's eventual generation will need."},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "output_type", "priority", "purpose", "audience", "message_focus",
                    "tone", "cta_direction", "required_context", "constraints",
                ],
                "additionalProperties": False,
            },
        },
        "excluded_outputs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "output_type": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["output_type", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["strategy", "outputs", "excluded_outputs"],
    "additionalProperties": False,
}


def _truncate(text: str, limit: int = MAX_TURN_TEXT_CHARS_IN_PROMPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_content_planning_user_prompt(
    *,
    analysis,
    clarification_context,
    conversation_turns: list,
    max_outputs: int,
) -> str:
    """Construct the bounded user-turn content for one planning request.
    `analysis` is the persisted AnalysisResult (or None); `clarification_
    context` is the persisted ClarificationContext (or None, if the
    workflow reached GENERATING_CONTENT with zero questions asked)."""

    parts = ["Media analysis:"]
    if analysis is not None:
        parts += [
            f"- summary: {analysis.summary}",
            f"- visible subjects: {', '.join(analysis.visible_subjects) or '(none noted)'}",
            f"- visual style: {', '.join(analysis.visual_style) or '(none noted)'}",
            f"- dominant themes: {', '.join(analysis.dominant_themes) or '(none noted)'}",
            f"- brand signals: {', '.join(analysis.brand_signals) or '(none noted)'}",
            f"- content opportunities: {', '.join(analysis.content_opportunities) or '(none noted)'}",
        ]
    else:
        parts.append("(not available)")

    parts += ["", "Known context from clarification:"]
    if clarification_context is not None:
        any_known = False
        for name in _CLARIFICATION_SCALAR_FIELDS:
            field_value = getattr(clarification_context, name)
            if field_value is not None:
                parts.append(f"- {name}: {field_value.value} (source: {field_value.source.value})")
                any_known = True
        if clarification_context.platforms is not None:
            parts.append(f"- platforms: {', '.join(clarification_context.platforms.value)}")
            any_known = True
        for key, value in clarification_context.factual_context.items():
            parts.append(f"- fact ({key}): {value}")
            any_known = True
        if not any_known:
            parts.append("(nothing beyond the media analysis)")
    else:
        parts.append("(no clarification was needed for this post)")

    recent_turns = conversation_turns[-MAX_CONVERSATION_TURNS_IN_PROMPT:]
    if recent_turns:
        parts += ["", f"Recent conversation (most recent {len(recent_turns)} turns):"]
        parts += [f"- {t.get('role')}: {_truncate(str(t.get('content', '')))}" for t in recent_turns]

    parts += [
        "",
        "Registered output types you may choose from (choose only from this list):",
        _format_output_registry(),
        "",
        f"Maximum outputs allowed in this plan: {max_outputs}. Recommend fewer if fewer are genuinely justified.",
    ]

    return "\n".join(parts)
