import pytest

from src.publication.builder import build_media_references, build_publication_content
from src.publication.errors import MissingMediaForPublicationError, UnsupportedPublicationMediaTypeError
from src.publication.models import PublicationChannel


# --- build_publication_content ------------------------------------------


def test_build_publication_content_trims_whitespace():
    content = build_publication_content({"caption": "  Hello world  ", "hashtags": [], "cta": None})
    assert content.caption == "Hello world"


def test_build_publication_content_strips_leading_hash_from_hashtags():
    content = build_publication_content({"caption": "Hi", "hashtags": ["#nrc", "brand"], "cta": None})
    assert content.hashtags == ["nrc", "brand"]


def test_build_publication_content_never_rewrites_wording():
    caption = "This is the exact approved wording, unchanged."
    content = build_publication_content({"caption": caption, "hashtags": [], "cta": None})
    assert content.caption == caption


def test_build_publication_content_trims_cta_and_treats_blank_as_none():
    content = build_publication_content({"caption": "Hi", "hashtags": [], "cta": "   "})
    assert content.cta is None


def test_build_publication_content_handles_missing_fields():
    content = build_publication_content({})
    assert content.caption == ""
    assert content.hashtags == []
    assert content.cta is None


# --- build_media_references ---------------------------------------------


def test_build_media_references_returns_exactly_one_entry_for_supported_photo():
    workflow_media = {"telegram_file_unique_id": "tg-1", "mime_type": "image/jpeg", "s3_key": "media/wf-1/photo.jpg"}

    refs = build_media_references(workflow_media, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")

    assert len(refs) == 1
    assert refs[0].asset_id == "tg-1"
    assert refs[0].media_type == "image/jpeg"
    assert refs[0].storage_reference == {"bucket": "my-bucket", "key": "media/wf-1/photo.jpg"}


def test_build_media_references_allows_video_ahead_of_the_analysis_gate_lifting():
    workflow_media = {"telegram_file_unique_id": "tg-2", "mime_type": "video/mp4", "s3_key": "media/wf-1/video.mp4"}

    refs = build_media_references(workflow_media, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")

    assert refs[0].media_type == "video/mp4"


def test_build_media_references_falls_back_to_s3_key_as_asset_id():
    workflow_media = {"mime_type": "image/jpeg", "s3_key": "media/wf-1/photo.jpg"}

    refs = build_media_references(workflow_media, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")

    assert refs[0].asset_id == "media/wf-1/photo.jpg"


def test_build_media_references_rejects_empty_media_record():
    with pytest.raises(MissingMediaForPublicationError):
        build_media_references({}, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")


def test_build_media_references_rejects_missing_s3_key():
    with pytest.raises(MissingMediaForPublicationError):
        build_media_references(
            {"mime_type": "image/jpeg"}, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket"
        )


def test_build_media_references_rejects_unsupported_media_type():
    workflow_media = {"mime_type": "audio/mpeg", "s3_key": "media/wf-1/audio.mp3"}

    with pytest.raises(UnsupportedPublicationMediaTypeError):
        build_media_references(workflow_media, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")


def test_build_media_references_never_includes_a_temporary_telegram_url():
    workflow_media = {
        "mime_type": "image/jpeg", "s3_key": "media/wf-1/photo.jpg",
        "telegram_file_url": "https://api.telegram.org/file/bot123/photo.jpg",
    }

    refs = build_media_references(workflow_media, channel=PublicationChannel.INSTAGRAM, bucket_name="my-bucket")

    assert "telegram_file_url" not in refs[0].storage_reference
    assert refs[0].storage_reference["key"] == "media/wf-1/photo.jpg"
