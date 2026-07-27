"""Versioned prompts and response schemas for the Primary Draft Generation
Engine (Milestone 6), keyed by output type — only `instagram_reel_caption`
is implemented, the sole registry entry with `supports_generation = True`
(see content_plan_models.py). Adding generation support for a second
output type means adding its own prompt/schema pair here, not bending this
one to fit multiple formats.

DRAFT_PROMPT_VERSION covers every output type's prompt+schema together,
recorded on every persisted draft (`prompt_version`). Distinct from
DRAFT_SCHEMA_VERSION (config.py, the persisted document's own storage
schema) and every other pipeline's version numbers — all change
independently.

Static brand-voice guidance below is a deliberate choice made *by this
milestone* (no prior project document defines NRC's voice principles) —
flagged in README.md/the completion report as a candidate for a future
DECISIONS.md entry, not presented as pre-existing approved doctrine.
"""

from __future__ import annotations

DRAFT_PROMPT_VERSION = 1

MAX_CONVERSATION_TURNS_IN_PROMPT = 6
MAX_TURN_TEXT_CHARS_IN_PROMPT = 400

# Established here, not found elsewhere in the repo — see module docstring.
BRAND_VOICE_PRINCIPLES = (
    "confident", "premium", "clear", "human", "specific",
    "non-generic", "restrained rather than exaggerated",
    "impactful without empty marketing language",
)

_SHARED_RULES = """You are writing on behalf of NRC. You are a writer executing an already-decided strategy — never a strategist, and never allowed to revisit what should be created.

The planning decision is authoritative. Do not reconsider which output should be generated, do not change the target platform or output type, and do not add, remove, or hint at any other output. Produce exactly one draft for exactly the selected output — no variants, no alternates, no "option A / option B", no secondary platform content appended to your answer.

Ground every claim in the context you are given: the media analysis, the structured clarification context, explicit user corrections, and the content plan's strategy for this specific output. Explicit user statements always outrank anything inferred from the media — if the user corrected an assumption, honor the correction completely and do not reintroduce the original assumption. Never invent performance figures, growth percentages, client results, awards, customer counts, campaign outcomes, dates, locations, names, job titles, partnerships, testimonials, product features, service capabilities, rankings, market leadership claims, or guarantees. Where no factual evidence exists, use language that does not claim it. Do not identify a real person from the media — use neutral terms (founder, team member, subject, creative professional) unless a name or role was given to you explicitly.

Follow the plan's cta_direction exactly. Do not add a call to action the plan doesn't call for, and never use a generic sales phrase ("DM us now", "Book a call today", "Click the link in bio", "Contact us", "Follow for more") unless the plan and context actually support it. If the plan or context says no hard sell, write nothing that reads as one.

Write like NRC's voice: """ + ", ".join(BRAND_VOICE_PRINCIPLES) + """. Avoid generic agency language that could describe almost any brand or asset ("We are excited to share our journey", "Innovation meets creativity", "Taking brands to the next level", "Stay tuned for more", "Your vision, our passion", "We help brands grow") — write something only this specific asset, message, and context could have produced. Do not pad for length or repeat the same idea in different words.

Adapt your structure to the specific asset, output type, purpose, tone, audience, and message — there is no fixed skeleton (no mandatory hook/problem/solution/CTA sequence, no universal template). An educational post may need clear explanation; a founder introduction may need a personal opening; a work showcase may need minimal, visual-first copy; a campaign launch may need momentum. Decide the right shape for this draft specifically.

Do not use hashtags unless they add real, specific value — never generic filler tags, never invented campaign hashtags, never padding. Do not add emoji automatically; use them only if they genuinely suit the tone and asset — a restrained, premium draft may reasonably use none.

Never reveal hidden reasoning, chain-of-thought, or an explanation of your choices. Never mention internal terminology (content plan, analysis, clarification, workflow, output type, schema, confidence, Claude, Anthropic, S3, model settings) anywhere in the copy itself. Return only the structured fields requested, with no markdown code fences, no commentary, and no placeholder or bracketed instruction text.
"""

INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT = _SHARED_RULES + """
You are writing the caption for one Instagram Reel. Keep it readable at a glance, suited to how people scroll Instagram — concise paragraphs or short lines, not a wall of text. The caption is the entire deliverable; there is no separate video script to write.
"""

DRAFT_GENERATION_SYSTEM_PROMPTS = {
    "instagram_reel_caption": INSTAGRAM_REEL_CAPTION_SYSTEM_PROMPT,
}

INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "caption": {
            "type": "string",
            "description": "The complete Reel caption body — the entire deliverable. Never markdown, never a code fence, never a bracketed instruction.",
        },
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "A small set of genuinely relevant hashtags, or an empty array if none add real value. Never generic filler tags.",
        },
        "cta": {
            "type": "string",
            "description": "The call-to-action text, following the plan's cta_direction exactly. Empty string if the plan calls for no CTA.",
        },
    },
    "required": ["caption", "hashtags", "cta"],
    "additionalProperties": False,
}

DRAFT_RESPONSE_SCHEMAS = {
    "instagram_reel_caption": INSTAGRAM_REEL_CAPTION_RESPONSE_SCHEMA,
}


def _truncate(text: str, limit: int = MAX_TURN_TEXT_CHARS_IN_PROMPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_draft_generation_user_prompt(
    *,
    analysis,
    clarification_context,
    conversation_turns: list,
    strategy,
    output,
) -> str:
    """Construct the bounded user-turn content for one generation request.
    Only the selected priority-1 `output` and the shared `strategy` are
    included — no secondary/excluded outputs, no raw media, no S3 paths,
    no Telegram metadata."""

    parts = ["Media analysis:"]
    if analysis is not None:
        parts += [
            f"- summary: {analysis.summary}",
            f"- visible subjects: {', '.join(analysis.visible_subjects) or '(none noted)'}",
            f"- visual style: {', '.join(analysis.visual_style) or '(none noted)'}",
            f"- dominant themes: {', '.join(analysis.dominant_themes) or '(none noted)'}",
        ]
    else:
        parts.append("(not available)")

    parts += ["", "Known context from clarification:"]
    if clarification_context is not None:
        any_known = False
        for name in (
            "brand_name", "content_type", "objective", "audience",
            "message_focus", "tone", "call_to_action",
        ):
            field_value = getattr(clarification_context, name)
            if field_value is not None:
                parts.append(f"- {name}: {field_value.value} (source: {field_value.source.value})")
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
        "Content strategy for this post (already decided — do not reconsider it):",
        f"- content type: {strategy.content_type}",
        f"- primary objective: {strategy.primary_objective}",
        f"- audience: {', '.join(strategy.audience) or '(not specified)'}",
        f"- central message: {strategy.central_message}",
        f"- brand positioning: {strategy.brand_positioning}",
        f"- tone direction: {', '.join(strategy.tone_direction) or '(not specified)'}",
        f"- factual constraints: {', '.join(strategy.factual_constraints) or '(none)'}",
        f"- avoid: {', '.join(strategy.avoid) or '(nothing specific)'}",
        "",
        "The selected output to generate (already decided — do not change the output type or platform):",
        f"- output type: {output.output_type}",
        f"- purpose: {output.purpose}",
        f"- audience: {', '.join(output.audience) or '(not specified)'}",
        f"- message focus: {output.message_focus}",
        f"- tone: {', '.join(output.tone) or '(not specified)'}",
        f"- cta direction: {output.cta_direction or '(no CTA)'}",
        f"- constraints: {', '.join(output.constraints) or '(none)'}",
    ]

    return "\n".join(parts)
