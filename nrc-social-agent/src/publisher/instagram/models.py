"""Instagram-specific container-mode mapping.

**Milestone 11A change**: this module no longer infers anything from a
raw media MIME type — that inference now happens exactly once, at
publication-package build time (see `src/publication/models.py`'s
`resolve_placement()`), and is persisted on the package as an explicit
`placement`/`media_mode` record. This module only ever maps that
*already-resolved* `Placement` to the Instagram-specific container mode
Meta's Content Publishing API expects — it does not, and must not,
re-derive placement from `MediaAssetReference.media_type` itself. See
the Milestone 11A planning report and README.md's "Explicit placement
contract" section for why: `output_type=instagram_reel_caption` never
reliably implied Reel placement on its own, and repeatedly re-inferring
the same fact at publish time (rather than trusting what was already
decided and persisted upstream) was exactly the residual risk that
report flagged.
"""

from __future__ import annotations

from enum import Enum

from ...execution.errors import ExecutionPermanentError
from ...publication.models import Placement


class UnsupportedInstagramMediaTypeError(ExecutionPermanentError):
    """The package's persisted placement has no Instagram container-mode
    mapping — fails safely before any Meta call rather than guessing
    IMAGE or REELS."""

    user_message = "This media type isn't supported for Instagram publishing yet."


class InstagramMediaMode(str, Enum):
    IMAGE = "IMAGE"
    REELS = "REELS"


_MODE_BY_PLACEMENT = {
    Placement.FEED: InstagramMediaMode.IMAGE,
    Placement.REEL: InstagramMediaMode.REELS,
}


def resolve_instagram_media_mode(placement: Placement) -> InstagramMediaMode:
    """Deterministic, code-owned mapping from the package's already-
    persisted `Placement` to the Instagram container mode — never
    inferred from a raw MIME type at this layer, never a default
    fallback for an unrecognized placement."""

    mode = _MODE_BY_PLACEMENT.get(placement)
    if mode is None:
        raise UnsupportedInstagramMediaTypeError(f"placement={placement!r} has no Instagram container-mode mapping")
    return mode
