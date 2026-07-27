"""Versioned prompt for the Draft Editing Service (Milestone 7), keyed by
output type — only `instagram_reel_caption` is implemented, mirroring
draft_prompts.py's identical scope limit (the sole registry entry with
`supports_generation = True`; editing is only ever offered for a draft
that was itself generated, so the same limit applies transitively).

Reuses draft_prompts.py's response schema unchanged (`caption`/`hashtags`/
`cta`, `additionalProperties: False`) — an edit produces exactly the same
shape as a fresh generation, just derived from the current draft plus an
instruction instead of from scratch. No separate schema module needed.

EDIT_PROMPT_VERSION covers this prompt+schema pairing, recorded on every
persisted version's `prompt_version` — distinct from DRAFT_PROMPT_VERSION
(the original-generation prompt) and every other pipeline's version
numbers, all of which change independently.
"""

from __future__ import annotations

from .draft_prompts import BRAND_VOICE_PRINCIPLES, DRAFT_RESPONSE_SCHEMAS

EDIT_PROMPT_VERSION = 1

MAX_INSTRUCTION_CHARS_IN_PROMPT = 600

_SHARED_EDIT_RULES = """You are revising an existing, already-reviewed draft on behalf of NRC, according to one specific instruction from the person reviewing it. You are not a strategist and not starting from scratch — the output type, target platform, and underlying strategy were already decided and are not yours to reconsider.

The selected output type and target platform are authoritative and must not change. Return exactly one revised draft — no variants, no alternates, no "option A / option B", no secondary platform content, no commentary, no explanation, and no hidden reasoning. If the instruction implicitly asks for a different platform or a second output (e.g. "turn this into a LinkedIn post," "also make an Instagram feed caption"), do not attempt it — instead produce the best faithful revision of the CURRENT output only, informed by whatever part of the instruction still applies to it; the surrounding application will detect and reject an actual platform/output-type switch, so do not try to satisfy that part of the request.

Follow the latest edit instruction as the primary authority for what changes. It may change wording, structure, length, CTA, hashtags, emoji use, tone, emphasis, opening, ending, or level of detail. It must never override: the target output type or platform, established facts, the identity-protection rule below, or the plan's factual constraints. Preserve everything about the current draft the instruction doesn't ask you to change — especially for a targeted, narrow instruction (e.g. "remove the hashtags"): do not rewrite the caption's wording just because you were asked to touch a different field. Perform a broad rewrite only when the instruction clearly calls for one (e.g. "rewrite it completely").

Never invent performance figures, growth percentages, client results, awards, customer counts, campaign outcomes, dates, locations, names, job titles, partnerships, testimonials, product features, service capabilities, rankings, market leadership claims, or guarantees, even if the instruction seems to invite it. Do not identify a real person from the media — use neutral terms (founder, team member, subject, creative professional) unless a name or role was already established.

If the instruction asks to remove the CTA, the revised cta must be empty. If it asks to remove hashtags, the revised hashtags must be empty. If it asks for a shorter draft, the revised caption must be meaningfully shorter than the current one, not merely reworded. If it asks to preserve a specific line or phrase, keep it recognizably intact. Never add a generic sales phrase ("DM us now", "Book a call today", "Click the link in bio", "Contact us", "Follow for more") unless it was already present and the instruction doesn't ask to remove it.

Write like NRC's voice: """ + ", ".join(BRAND_VOICE_PRINCIPLES) + """. Never mention internal terminology (content plan, analysis, clarification, workflow, output type, schema, version, draft, edit instruction, confidence, Claude, Anthropic, S3, model settings) anywhere in the copy itself. Return only the structured fields requested, with no markdown code fences and no placeholder or bracketed instruction text.
"""

INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT = _SHARED_EDIT_RULES + """
You are revising the caption for one Instagram Reel. Keep it readable at a glance, suited to how people scroll Instagram — concise paragraphs or short lines, not a wall of text. The caption is the entire deliverable; there is no separate video script.
"""

DRAFT_EDIT_SYSTEM_PROMPTS = {
    "instagram_reel_caption": INSTAGRAM_REEL_CAPTION_EDIT_SYSTEM_PROMPT,
}

# Reused unchanged from draft_prompts.py — see module docstring.
DRAFT_EDIT_RESPONSE_SCHEMAS = DRAFT_RESPONSE_SCHEMAS


def _truncate(text: str, limit: int = MAX_INSTRUCTION_CHARS_IN_PROMPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_draft_edit_user_prompt(
    *,
    current_content: dict,
    instruction: str,
    strategy,
    output,
    analysis,
    clarification_context,
    current_version_number: int,
) -> str:
    """Construct the bounded user-turn content for one edit request. Only
    the CURRENT draft's content, the user's instruction, and the shared
    strategy/selected-output are included in full — analysis and
    clarification context are included only as brief grounding, not the
    full conversation history (the instruction itself is the primary
    signal for an edit, unlike a fresh generation)."""

    parts = [
        "You are revising version "
        f"{current_version_number} of an already-generated draft. Do not start over from the media — "
        "revise the text below according to the instruction.",
        "",
        "Current draft content (revise this, not from scratch):",
        f"- caption: {current_content.get('caption', '')}",
        f"- hashtags: {', '.join(current_content.get('hashtags') or []) or '(none)'}",
        f"- cta: {current_content.get('cta') or '(none)'}",
        "",
        "The reviewer's edit instruction (the primary authority for what to change):",
        _truncate(instruction),
        "",
        "Content strategy for this post (already decided — do not reconsider it):",
        f"- content type: {strategy.content_type}",
        f"- primary objective: {strategy.primary_objective}",
        f"- central message: {strategy.central_message}",
        f"- brand positioning: {strategy.brand_positioning}",
        f"- factual constraints: {', '.join(strategy.factual_constraints) or '(none)'}",
        f"- avoid: {', '.join(strategy.avoid) or '(nothing specific)'}",
        "",
        "The selected output (already decided — do not change the output type or platform):",
        f"- output type: {output.output_type}",
        f"- purpose: {output.purpose}",
        f"- message focus: {output.message_focus}",
        f"- cta direction: {output.cta_direction or '(no CTA)'}",
    ]

    parts += ["", "Media analysis (for grounding/fact-preservation only):"]
    if analysis is not None:
        parts.append(f"- summary: {analysis.summary}")
    else:
        parts.append("(not available)")

    parts += ["", "Known context from clarification (for grounding/fact-preservation only):"]
    if clarification_context is not None:
        any_known = False
        for name in ("brand_name", "content_type", "objective", "audience", "message_focus", "tone", "call_to_action"):
            field_value = getattr(clarification_context, name)
            if field_value is not None:
                parts.append(f"- {name}: {field_value.value}")
                any_known = True
        for key, value in clarification_context.factual_context.items():
            parts.append(f"- fact ({key}): {value}")
            any_known = True
        if not any_known:
            parts.append("(nothing beyond the media analysis)")
    else:
        parts.append("(no clarification was needed for this post)")

    return "\n".join(parts)
