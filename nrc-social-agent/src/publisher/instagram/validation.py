"""Instagram-specific preflight validation — run as the very first step
of `InstagramPublisher.advance()` (the `NOT_STARTED` checkpoint), always
*before* any Meta call. A permanent failure here therefore makes zero
Meta calls, satisfying this milestone's "permanent preflight failures
should make zero Meta calls" requirement for everything specific to
Instagram (generic execution/publication linkage checks already ran one
layer up, in `src/execution/dispatch_service.py`, before the publisher
was ever invoked at all).
"""

from __future__ import annotations

from ...publication.models import MediaAssetReference, PublicationPackage
from ..errors import PublisherPermanentError

# Meta's own documented Instagram caption limit (2200 characters) —
# already approximately enforced upstream by Milestone 8's
# MAX_INSTAGRAM_CAPTION_LENGTH, but re-checked here against the *exact*
# assembled text Meta will actually receive (caption + CTA paragraph +
# hashtag line), which is a few characters longer than the sum of the
# individual fields due to the deterministic paragraph separators.
INSTAGRAM_CAPTION_LIMIT = 2200


def validate_single_media_reference(publication: PublicationPackage) -> MediaAssetReference:
    if not publication.media or len(publication.media) != 1:
        raise PublisherPermanentError(
            f"expected exactly one media reference, found {len(publication.media or [])}"
        )
    entry = publication.media[0]
    return entry if isinstance(entry, MediaAssetReference) else MediaAssetReference.from_dict(entry)


def validate_assembled_caption(caption: str) -> None:
    if not caption.strip():
        raise PublisherPermanentError("assembled caption is empty")
    if len(caption) > INSTAGRAM_CAPTION_LIMIT:
        raise PublisherPermanentError(
            f"assembled caption is {len(caption)} characters, exceeding Instagram's {INSTAGRAM_CAPTION_LIMIT}-character limit"
        )


def validate_credentials(*, instagram_account_id: str | None, access_token: str | None) -> None:
    if not instagram_account_id or not access_token:
        raise PublisherPermanentError("Instagram publishing credentials are not configured")
