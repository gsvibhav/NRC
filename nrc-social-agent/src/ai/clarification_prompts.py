"""Versioned prompt and response schema for the adaptive clarification
decision (Milestone 4B).

Deliberate design choice: **one unified decision prompt**, not three
separate ones. The brief allows "separate prompts or a carefully designed
unified policy" for (initial decision / answer interpretation / next
question). A single call that both interprets the latest answer (if any)
and decides the next step:

- halves the Claude calls per turn (cost, latency, and fewer places for
  the two steps to disagree with each other),
- is what `src/ai/client.py`'s `decide_clarification()` and
  `clarification_service.py`'s single `_run_decision_cycle()` are built
  around,
- and is exactly as capable: `context_updates` naturally comes back empty
  on the first cycle (nothing to interpret yet) and populated on every
  cycle that had a user answer to interpret.

CLARIFICATION_POLICY_VERSION covers this prompt + CLARIFICATION_RESPONSE_
SCHEMA together, recorded on every persisted `pending_question` (see
workflow/models.py) — bump it whenever a wording or schema change could
shift Claude's decisions or output shape. Distinct from
CLARIFICATION_CONTEXT_VERSION (clarification_models.py, the persisted
context's own schema) and ANALYSIS_SCHEMA_VERSION/ANALYSIS_PROMPT_VERSION
(the unrelated media-analysis pipeline) — all four version numbers change
independently.
"""

from __future__ import annotations

CLARIFICATION_POLICY_VERSION = 1

# Bounded conversation-context construction: only the most recent turns go
# into the prompt verbatim. The full accumulated understanding already
# lives in the structured context (always sent in full), so older raw
# turns add little beyond "what's already been asked" — which
# `remaining_uncertainties` on each decision, still available at any
# turn, already keeps fresh without re-sending the entire transcript.
MAX_CONVERSATION_TURNS_IN_PROMPT = 6

# Above this, the question or answer text is truncated in the prompt (not
# in what's persisted) — a defensive bound in case of an unusually long
# reply, so one message can't blow out the request.
MAX_TURN_TEXT_CHARS_IN_PROMPT = 600

# Mirrors clarification_models._SCALAR_FIELDS — kept as its own tuple here
# rather than importing that module's private name, so this module stays
# a plain consumer of ClarificationContext's public shape (duck-typed via
# getattr) rather than reaching into its internals.
_CONTEXT_SCALAR_FIELDS = (
    "brand_name",
    "content_type",
    "objective",
    "audience",
    "message_focus",
    "tone",
    "call_to_action",
)


CLARIFICATION_SYSTEM_PROMPT = """You are a thoughtful creative strategist helping NRC prepare a social media post from a piece of uploaded media. A separate step has already analyzed the media; you are deciding, turn by turn, whether you have enough understanding to move on to writing the post, or whether one more thing is worth asking about first.

You are not a form. Never walk through a fixed checklist of topics. Decide fresh, every turn, from the specific media analysis, the structured context already known, and the conversation so far, what — if anything — would most change the resulting post if you knew it.

Core rules:
- Ask about at most one thing per turn. If several things are uncertain, ask about only the single highest-impact one; the rest can wait for a later turn or may never need asking if they turn out not to matter.
- Only ask when the answer would materially affect the content's purpose, message emphasis, brand positioning, audience framing, tone, call to action, factual accuracy, platform suitability, post type, or campaign context. Do not ask to seem thorough.
- Never ask about anything already visible in the media, already present in the analysis, already present in the known context, already answered (even indirectly) in an earlier reply, or safely inferable without asking.
- When priorities compete, prefer in roughly this order, but let the specific media and prior answers override it: factual ambiguity that could make the content wrong; the purpose or intended outcome; the type of post; brand/product/campaign/person context; audience; message emphasis; tone; call to action; optional stylistic preference.
- Every question must be grounded in specifics from this particular upload and analysis. Never ask a question that could be asked about any upload with the words changed — reference what is actually visible or already understood.
- Write questions the way a sharp, warm colleague would ask them — natural language, concise, easy to answer in a sentence or two. No robotic form language, no "Question 1", no "Please select", no field names, no mention of JSON, schemas, states, or confidence.
- You may offer two or three illustrative options inside a question to give the user direction (e.g. "...more editorial, or more high-energy?"), but always leave room for the user to answer completely differently. Never present a rigid multiple-choice menu.
- Extract everything useful from the user's latest reply, not just the literal answer to your last question — a single reply can resolve several things at once. Update every field it touches.
- Treat the user's explicit statements as authoritative. If a reply contradicts something inferred from the media or assumed earlier, the user's statement wins — update the context accordingly and never argue with it based on the original analysis.
- If a reply is ambiguous, interpret it using the pending question and prior context; only ask a follow-up if the ambiguity is genuinely material, and phrase it as a natural clarification, not "invalid input."
- If a reply doesn't address the pending question at all, do not treat it as an answer to that question — acknowledge it briefly and naturally, then still ask (or re-ask, reformulated if useful) whatever is still genuinely needed.
- If the user signals they want you to proceed with your own judgement (in any natural phrasing — e.g. wanting to move on, deferring to you, saying that's enough), treat that as a strong signal to stop asking optional questions and proceed with the best available grounded context. Only ask one more time if a specific fact is still essential and cannot be safely skipped — and if so, briefly explain in the question itself why that particular detail matters.
- Never invent brand names, campaign facts, people's names or identities, locations, or any other fact not established by the media, the analysis, or an explicit user statement. If something is unknown and not essential, leave it unresolved rather than guessing.
- Do not identify specific real people from the media or infer sensitive personal attributes. Use neutral terms (founder, person, team member, subject) unless the user's own words supply a name or role.
- Stop as soon as you have enough grounded understanding to write a strong, accurate post — do not keep asking to be thorough for its own sake. Zero questions is a completely valid outcome when the media and its analysis already make the post's purpose clear.
- Never output hidden reasoning, chain-of-thought, or private notes anywhere in your response. The "purpose" you return for a question is a short operational label only (e.g. "clarify primary objective"), not an explanation of your thinking.
- Return only the structured fields requested — no additional commentary.

You will be told, each turn, how many clarification questions have already been asked and the maximum allowed. If that maximum has already been reached, you must not choose to ask another question under any circumstance — decide to continue instead. If you genuinely believe essential, safety-relevant context is still missing at that point, say so plainly by setting context_sufficient to false and describing the gap in remaining_uncertainties, but still return the continue decision; you will not be asked again this conversation.
"""


CLARIFICATION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "description": "Either 'ASK_QUESTION' (one more clarification is worth the friction) or 'CONTINUE' (enough is known, or the question limit has been reached).",
        },
        "context_sufficient": {
            "type": "boolean",
            "description": "True if there is enough grounded context to produce a strong, accurate post right now.",
        },
        "question_text": {
            "type": "string",
            "description": "The single natural-language question to ask, grounded in this specific upload. Empty string if decision is CONTINUE.",
        },
        "question_purpose": {
            "type": "string",
            "description": "A short operational label for why this question is being asked (e.g. 'clarify primary objective'), not an explanation of your reasoning. Empty string if decision is CONTINUE.",
        },
        "question_target_field": {
            "type": "string",
            "description": "Which context category this question mainly targets: one of brand_name, content_type, objective, audience, message_focus, tone, call_to_action, platforms, factual_context, other. Empty string if decision is CONTINUE.",
        },
        "context_updates": {
            "type": "object",
            "properties": {
                "brand_name": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "content_type": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "objective": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "audience": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "message_focus": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "tone": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "call_to_action": {"type": "string", "description": "New or updated value, empty string if unchanged this turn."},
                "platforms": {"type": "array", "items": {"type": "string"}, "description": "New or updated platform list, empty array if unchanged this turn."},
                "factual_context": {"type": "object", "description": "Any newly established facts as short key/value pairs, e.g. {\"event_name\": \"...\"}. Empty object if none."},
                "user_preferences": {"type": "object", "description": "Any stylistic or process preferences the user expressed. Empty object if none."},
            },
            "required": [
                "brand_name", "content_type", "objective", "audience", "message_focus",
                "tone", "call_to_action", "platforms", "factual_context", "user_preferences",
            ],
            "additionalProperties": False,
            "description": "Everything newly known or changed as of this turn's reply. Leave fields empty/unchanged if this turn didn't touch them.",
        },
        "remaining_uncertainties": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short labels of what's still unresolved, whether or not you're asking about it now.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0: how confident you are that context_sufficient is correctly assessed.",
        },
    },
    "required": [
        "decision", "context_sufficient", "question_text", "question_purpose",
        "question_target_field", "context_updates", "remaining_uncertainties", "confidence",
    ],
    "additionalProperties": False,
}


def _truncate(text: str, limit: int = MAX_TURN_TEXT_CHARS_IN_PROMPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_context(context) -> str:
    lines = []
    for name in _CONTEXT_SCALAR_FIELDS:
        field_value = getattr(context, name)
        if field_value is not None:
            lines.append(f"- {name}: {field_value.value} (source: {field_value.source.value})")
    if context.platforms is not None:
        lines.append(f"- platforms: {', '.join(context.platforms.value)} (source: {context.platforms.source.value})")
    for key, value in context.factual_context.items():
        lines.append(f"- fact ({key}): {value}")
    for key, value in context.user_preferences.items():
        lines.append(f"- preference ({key}): {value}")
    if not lines:
        return "(nothing known yet beyond the media analysis)"
    return "\n".join(lines)


def build_clarification_user_prompt(
    *,
    analysis_summary: str | None,
    context,
    conversation_turns: list,
    pending_question: dict | None,
    question_count: int,
    max_questions: int,
) -> str:
    """Construct the bounded user-turn content for one decision cycle.
    `conversation_turns` is the workflow's full persisted history — only
    the most recent MAX_CONVERSATION_TURNS_IN_PROMPT are included
    verbatim; the accumulated understanding beyond that lives in
    `context`, sent in full regardless of conversation length. The latest
    user answer, if any, is derived from `conversation_turns` itself (the
    last turn, if it's a user turn) rather than passed separately — this
    is what makes the same prompt correct whether this is the very first
    cycle, a cycle following a fresh reply, or a /retry of either."""

    recent_turns = conversation_turns[-MAX_CONVERSATION_TURNS_IN_PROMPT:]
    turns_text = "\n".join(
        f"- {turn.get('role')}: {_truncate(str(turn.get('content', '')))}" for turn in recent_turns
    ) or "(no turns yet)"

    latest_answer = None
    if conversation_turns and conversation_turns[-1].get("role") == "user":
        latest_answer = conversation_turns[-1].get("content")

    parts = [
        f"Media analysis summary: {analysis_summary or '(not available)'}",
        "",
        "Known context so far:",
        _format_context(context),
        "",
        f"Recent conversation (most recent {len(recent_turns)} turns):",
        turns_text,
    ]

    if pending_question is not None:
        parts += ["", f"Question currently pending an answer: {_truncate(pending_question.get('text', ''))}"]

    if latest_answer is not None:
        parts += ["", f"Latest user reply to interpret: {_truncate(str(latest_answer))}"]

    parts += [
        "",
        f"Clarification questions asked so far: {question_count} of a maximum {max_questions}.",
    ]
    if question_count >= max_questions:
        parts.append("The maximum has been reached — you must return CONTINUE, not ASK_QUESTION.")

    return "\n".join(parts)
