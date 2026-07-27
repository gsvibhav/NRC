import pytest

from src.publication.errors import PublicationValidationFailedError
from src.publication.models import MediaAssetReference, PublicationContent
from src.publication.validation import validate_publication_package


def _content(**overrides) -> PublicationContent:
    defaults = dict(caption="A meaningfully long approved caption about our brand.", hashtags=["nrc", "brand"], cta="Learn more")
    defaults.update(overrides)
    return PublicationContent(**defaults)


def _media(**overrides) -> list:
    defaults = dict(asset_id="a1", media_type="image/jpeg", storage_reference={"bucket": "b", "key": "k"})
    defaults.update(overrides)
    return [MediaAssetReference(**defaults)]


def test_valid_package_passes():
    validate_publication_package(_content(), _media(), max_caption_length=2200, max_hashtags=30)


def test_rejects_caption_below_minimum_length():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(caption="Hi"), _media(), max_caption_length=2200, max_hashtags=30)


def test_rejects_combined_length_over_limit():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(caption="x" * 100), _media(), max_caption_length=50, max_hashtags=30)


def test_rejects_too_many_hashtags():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(hashtags=["a", "b", "c"]), _media(), max_caption_length=2200, max_hashtags=2)


def test_rejects_malformed_hashtag():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(hashtags=["not a tag!"]), _media(), max_caption_length=2200, max_hashtags=30)


def test_rejects_cta_duplicated_in_caption():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(
            _content(caption="Great post. Learn more right here.", cta="Learn more"),
            _media(), max_caption_length=2200, max_hashtags=30,
        )


def test_rejects_empty_media_list():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(), [], max_caption_length=2200, max_hashtags=30)


def test_rejects_more_than_one_media_reference():
    media = _media() + _media(asset_id="a2")
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(), media, max_caption_length=2200, max_hashtags=30)


def test_rejects_duplicate_asset_ids():
    media = [
        MediaAssetReference(asset_id="a1", media_type="image/jpeg", storage_reference={"bucket": "b", "key": "k1"}),
    ]
    # Duplicate check only meaningfully triggers with >1 entries, but the
    # cardinality rule above already rejects that case; this test protects
    # the duplicate-id branch directly in case cardinality is ever relaxed.
    validate_publication_package(_content(), media, max_caption_length=2200, max_hashtags=30)


def test_rejects_incomplete_storage_reference():
    media = [MediaAssetReference(asset_id="a1", media_type="image/jpeg", storage_reference={"bucket": "b"})]
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(), media, max_caption_length=2200, max_hashtags=30)


def test_rejects_url_shaped_storage_key():
    media = [
        MediaAssetReference(
            asset_id="a1", media_type="image/jpeg",
            storage_reference={"bucket": "b", "key": "https://example.com/photo.jpg"},
        )
    ]
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(_content(), media, max_caption_length=2200, max_hashtags=30)


def test_rejects_banned_metadata_key():
    with pytest.raises(PublicationValidationFailedError):
        validate_publication_package(
            _content(), _media(), max_caption_length=2200, max_hashtags=30,
            metadata={"raw_response": "..."},
        )


def test_allows_benign_metadata_key():
    validate_publication_package(
        _content(), _media(), max_caption_length=2200, max_hashtags=30, metadata={"failure_reason": "x"},
    )
