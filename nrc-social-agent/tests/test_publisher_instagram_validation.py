import pytest

from src.publication.models import MediaAssetReference, PublicationChannel, PublicationPackage, PublicationStatus
from src.publisher.errors import PublisherPermanentError
from src.publisher.instagram.validation import (
    INSTAGRAM_CAPTION_LIMIT,
    validate_assembled_caption,
    validate_credentials,
    validate_single_media_reference,
)


def _make_package(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1, created_at="t", updated_at="t",
        media=[{"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


def test_validate_single_media_reference_returns_the_one_entry():
    media = validate_single_media_reference(_make_package())
    assert isinstance(media, MediaAssetReference)
    assert media.asset_id == "a1"


def test_validate_single_media_reference_rejects_empty_media():
    with pytest.raises(PublisherPermanentError):
        validate_single_media_reference(_make_package(media=[]))


def test_validate_single_media_reference_rejects_multiple_entries():
    media = [
        {"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k1"}},
        {"asset_id": "a2", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k2"}},
    ]
    with pytest.raises(PublisherPermanentError):
        validate_single_media_reference(_make_package(media=media))


def test_validate_assembled_caption_rejects_empty():
    with pytest.raises(PublisherPermanentError):
        validate_assembled_caption("   ")


def test_validate_assembled_caption_rejects_over_limit():
    with pytest.raises(PublisherPermanentError):
        validate_assembled_caption("x" * (INSTAGRAM_CAPTION_LIMIT + 1))


def test_validate_assembled_caption_accepts_valid_text():
    validate_assembled_caption("A perfectly normal caption.")


def test_validate_credentials_rejects_missing_account_id():
    with pytest.raises(PublisherPermanentError):
        validate_credentials(instagram_account_id=None, access_token="tok")


def test_validate_credentials_rejects_missing_token():
    with pytest.raises(PublisherPermanentError):
        validate_credentials(instagram_account_id="123", access_token=None)


def test_validate_credentials_accepts_both_present():
    validate_credentials(instagram_account_id="123", access_token="tok")
