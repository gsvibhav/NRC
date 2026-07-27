"""Deterministic Instagram caption assembly — the *only* place the
package's structured `content` (caption/CTA/hashtags) is turned into the
single text blob Meta's `media` endpoint receives as `caption`.

Mechanical only: no Claude, no rewriting, no new or removed hashtags, no
duplicated CTA, deterministic newline rules. Given the same
`PublicationContent`, this always produces byte-identical output — the
package itself is never touched (assembly reads it, never writes it),
and the assembled string is transient request data, never persisted onto
either the package or the execution document.
"""

from __future__ import annotations

from ...publication.models import PublicationContent


def assemble_instagram_caption(content: PublicationContent) -> str:
    """`caption\\n\\nCTA\\n\\n#tag1 #tag2` — CTA and the hashtag line are
    each their own paragraph, omitted entirely (not left as an empty
    line) when absent, exactly mirroring
    src/handlers.py's `_format_instagram_reel_caption_preview()` (the
    Telegram preview) so what the reviewer approved and what Instagram
    receives read the same way."""

    parts = [content.caption]
    if content.cta:
        parts.append(content.cta)
    if content.hashtags:
        parts.append(" ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags))
    return "\n\n".join(parts)
