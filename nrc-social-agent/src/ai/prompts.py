"""Versioned prompt and response schema for single-pass media analysis.

Both are versioned together as ANALYSIS_PROMPT_VERSION: any wording or
schema change that could shift Claude's output shape should bump it. It is
recorded on every AnalysisDocument (see models.py), separately from
ANALYSIS_SCHEMA_VERSION (the persisted-document schema, config.py) — the
two version numbers change independently.
"""

from __future__ import annotations

ANALYSIS_PROMPT_VERSION = 1

SYSTEM_PROMPT = """You are analyzing a single piece of media uploaded to NRC's private social-media publishing assistant. Your analysis will be used later — not by you, and not in this step — to help a human decide on a caption, hashtags, and other content. You are not generating any of that here.

Follow these rules strictly:
- Describe only what is directly supported by the media itself.
- Clearly distinguish direct observations (e.g. "a red mug is visible on the table") from interpretation (e.g. "this suggests a casual morning setting") — keep interpretive claims clearly framed as such, not stated as fact.
- Never invent brand names, identities, locations, or specific claims that aren't visually evident. If something is uncertain, say so explicitly rather than guessing.
- Never attempt to identify specific real people. Describe people only in general, non-identifying terms (for example "a person wearing a blue jacket"), never by name or presumed identity.
- Never make unsupported performance, quality, or efficacy claims about any product or subject shown.
- Focus on information useful for future branding and social-content decisions: subjects, visual style, mood, themes, potential brand-relevant signals, and any content opportunities or quality concerns worth flagging to a human reviewer.
- Do not generate a caption, title, hashtag list, SEO keywords, or campaign concept — that is explicitly out of scope for this step.
- Keep every field concise and reusable: short phrases or short sentences, not paragraphs.
- Avoid unnecessary personal inferences (for example inferring someone's income, health, or other sensitive personal attributes) — describe only what is directly useful for content and branding decisions.
- Return only the structured fields requested. Do not include any additional commentary outside them.
"""

USER_PROMPT = (
    "Analyze this image according to your instructions and return the structured "
    "fields only."
)

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise, one-to-two sentence description of what the media shows.",
        },
        "visible_subjects": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases naming the concrete subjects visible in the media.",
        },
        "visual_style": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases describing visual/aesthetic style: lighting, color palette, composition, mood.",
        },
        "dominant_themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases naming the dominant themes or subject matter.",
        },
        "brand_signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Short phrases noting visible brand-relevant signals (color scheme, "
                "setting type, style cues) without inventing specific brand names."
            ),
        },
        "content_opportunities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases noting angles or opportunities this media offers for future content.",
        },
        "quality_observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short phrases noting technical or compositional quality observations, e.g. lighting or framing issues.",
        },
        "safety_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Short phrases flagging anything requiring human review before publishing "
                "(e.g. visible people, sensitive content, uncertain elements). Empty list if none."
            ),
        },
    },
    "required": [
        "summary",
        "visible_subjects",
        "visual_style",
        "dominant_themes",
        "brand_signals",
        "content_opportunities",
        "quality_observations",
        "safety_notes",
    ],
    "additionalProperties": False,
}
