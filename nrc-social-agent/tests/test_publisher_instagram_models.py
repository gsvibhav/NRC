import pytest

from src.publication.models import Placement
from src.publisher.instagram.models import (
    InstagramMediaMode,
    UnsupportedInstagramMediaTypeError,
    resolve_instagram_media_mode,
)


def test_resolve_instagram_media_mode_maps_feed_to_image():
    assert resolve_instagram_media_mode(Placement.FEED) is InstagramMediaMode.IMAGE


def test_resolve_instagram_media_mode_maps_reel_to_reels():
    assert resolve_instagram_media_mode(Placement.REEL) is InstagramMediaMode.REELS


def test_resolve_instagram_media_mode_rejects_unknown_placement():
    with pytest.raises(UnsupportedInstagramMediaTypeError):
        resolve_instagram_media_mode(None)


def test_resolve_instagram_media_mode_never_infers_from_a_raw_media_type():
    # There is no media_type parameter at all — placement is consumed,
    # never re-derived from MediaAssetReference.media_type at this layer.
    import inspect

    params = inspect.signature(resolve_instagram_media_mode).parameters
    assert list(params.keys()) == ["placement"]
