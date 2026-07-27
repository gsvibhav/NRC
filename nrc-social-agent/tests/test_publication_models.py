import pytest

from src.publication.errors import (
    InvalidPublicationStatusError,
    PublicationDeserializationError,
    PublicationVersionMismatchError,
    UnsupportedPublicationMediaTypeError,
    UnsupportedPublicationOutputTypeError,
)
from src.publication.models import (
    MediaAssetReference,
    MediaMode,
    Placement,
    PublicationApproval,
    PublicationChannel,
    PublicationContent,
    PublicationDraftReference,
    PublicationPackage,
    PublicationPlacement,
    PublicationStatus,
    resolve_channel,
    resolve_placement,
)


def _make_package(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1,
        created_at="t1", updated_at="t1",
        draft={"draft_id": "draft-1", "version_number": 2},
        content={"caption": "A great caption.", "hashtags": ["nrc"], "cta": "Learn more"},
        media=[{"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}}],
        approval={"approved_at": "t0", "approved_by_telegram_user_id": 42},
        source_versions={"draft_schema_version": 2, "draft_document_version": 2},
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


# --- resolve_channel ---------------------------------------------------


def test_resolve_channel_maps_instagram_reel_caption():
    assert resolve_channel("instagram_reel_caption") is PublicationChannel.INSTAGRAM


def test_resolve_channel_rejects_unknown_output_type():
    with pytest.raises(UnsupportedPublicationOutputTypeError):
        resolve_channel("linkedin_post")


def test_resolve_channel_never_falls_back_silently():
    with pytest.raises(UnsupportedPublicationOutputTypeError):
        resolve_channel("")


# --- PublicationContent --------------------------------------------------


def test_publication_content_round_trips():
    content = PublicationContent(caption="Hello world", hashtags=["nrc", "brand"], cta="Learn more")
    assert PublicationContent.from_dict(content.to_dict()) == content


def test_publication_content_from_dict_rejects_missing_caption():
    with pytest.raises(PublicationDeserializationError):
        PublicationContent.from_dict({"hashtags": []})


def test_publication_content_from_dict_defaults_hashtags_and_cta():
    content = PublicationContent.from_dict({"caption": "Hi"})
    assert content.hashtags == []
    assert content.cta is None


def test_publication_content_from_dict_rejects_non_string_hashtag():
    with pytest.raises(PublicationDeserializationError):
        PublicationContent.from_dict({"caption": "Hi", "hashtags": [1, 2]})


# --- MediaAssetReference ---------------------------------------------------


def test_media_asset_reference_round_trips():
    ref = MediaAssetReference(asset_id="a1", media_type="image/jpeg", storage_reference={"bucket": "b", "key": "k"})
    assert MediaAssetReference.from_dict(ref.to_dict()) == ref


def test_media_asset_reference_rejects_incomplete_storage_reference():
    with pytest.raises(PublicationDeserializationError):
        MediaAssetReference.from_dict({"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b"}})


def test_media_asset_reference_rejects_missing_asset_id():
    with pytest.raises(PublicationDeserializationError):
        MediaAssetReference.from_dict({"media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}})


# --- PublicationDraftReference ---------------------------------------------


def test_publication_draft_reference_round_trips():
    ref = PublicationDraftReference(draft_id="draft-1", version_number=3)
    assert PublicationDraftReference.from_dict(ref.to_dict()) == ref


def test_publication_draft_reference_rejects_non_positive_version():
    with pytest.raises(PublicationDeserializationError):
        PublicationDraftReference.from_dict({"draft_id": "draft-1", "version_number": 0})


# --- PublicationApproval -----------------------------------------------


def test_publication_approval_round_trips():
    approval = PublicationApproval(approved_at="t0", approved_by_telegram_user_id=42)
    assert PublicationApproval.from_dict(approval.to_dict()) == approval


def test_publication_approval_rejects_bool_as_user_id():
    with pytest.raises(PublicationDeserializationError):
        PublicationApproval.from_dict({"approved_at": "t0", "approved_by_telegram_user_id": True})


# --- PublicationPackage ---------------------------------------------------


def test_publication_package_round_trips():
    package = _make_package()
    assert PublicationPackage.from_dict(package.to_dict()) == package


def test_publication_package_from_dict_rejects_unsupported_version():
    data = _make_package().to_dict()
    data["version"] = 999
    with pytest.raises(PublicationVersionMismatchError):
        PublicationPackage.from_dict(data)


def test_publication_package_from_dict_rejects_missing_required_field():
    data = _make_package().to_dict()
    del data["workflow_id"]
    with pytest.raises(PublicationDeserializationError):
        PublicationPackage.from_dict(data)


def test_publication_package_from_dict_rejects_unrecognized_channel():
    data = _make_package().to_dict()
    data["channel"] = "tiktok"
    with pytest.raises(PublicationDeserializationError):
        PublicationPackage.from_dict(data)


def test_publication_package_from_dict_rejects_unrecognized_status():
    data = _make_package().to_dict()
    data["status"] = "PUBLISHED"
    with pytest.raises(InvalidPublicationStatusError):
        PublicationPackage.from_dict(data)


def test_publication_package_defaults_media_and_metadata():
    package = _make_package(media=[], metadata={})
    assert package.to_dict()["media"] == []
    assert package.to_dict()["metadata"] == {}


def test_publication_package_never_has_a_field_for_full_workflow_or_prompt():
    # Structural contract-minimality: these field names must never exist
    # on the dataclass, so a full workflow/analysis/prompt could never be
    # attached even by accident.
    field_names = {f for f in _make_package().__dataclass_fields__}
    for forbidden in ("workflow", "analysis", "clarification_history", "prompt", "raw_response", "conversation"):
        assert forbidden not in field_names


# --- resolve_placement (Milestone 11A) --------------------------------------


def test_resolve_placement_maps_jpeg_to_feed_single_image():
    placement = resolve_placement(PublicationChannel.INSTAGRAM, "image/jpeg")
    assert placement.placement is Placement.FEED
    assert placement.media_mode is MediaMode.SINGLE_IMAGE
    assert placement.channel is PublicationChannel.INSTAGRAM


def test_resolve_placement_maps_mp4_to_reel_video():
    placement = resolve_placement(PublicationChannel.INSTAGRAM, "video/mp4")
    assert placement.placement is Placement.REEL
    assert placement.media_mode is MediaMode.VIDEO


def test_resolve_placement_rejects_unknown_media_type():
    with pytest.raises(UnsupportedPublicationMediaTypeError):
        resolve_placement(PublicationChannel.INSTAGRAM, "application/pdf")


# --- PublicationPlacement ----------------------------------------------------


def test_publication_placement_round_trips():
    placement = PublicationPlacement(channel=PublicationChannel.INSTAGRAM, placement=Placement.FEED, media_mode=MediaMode.SINGLE_IMAGE)
    assert PublicationPlacement.from_dict(placement.to_dict()) == placement


def test_publication_placement_from_dict_rejects_unrecognized_placement():
    with pytest.raises(PublicationDeserializationError):
        PublicationPlacement.from_dict({"channel": "instagram", "placement": "story", "media_mode": "single_image"})


def test_publication_placement_from_dict_rejects_unrecognized_media_mode():
    with pytest.raises(PublicationDeserializationError):
        PublicationPlacement.from_dict({"channel": "instagram", "placement": "feed", "media_mode": "carousel"})


# --- PublicationPackage.resolved_placement() / schema v2 migration ----------


def test_publication_package_persists_placement_field_schema_v2():
    package = _make_package(placement=PublicationPlacement(
        channel=PublicationChannel.INSTAGRAM, placement=Placement.FEED, media_mode=MediaMode.SINGLE_IMAGE,
    ).to_dict())
    assert package.version == 2
    data = package.to_dict()
    assert data["placement"] == {"channel": "instagram", "placement": "feed", "media_mode": "single_image"}
    round_tripped = PublicationPackage.from_dict(data)
    assert round_tripped == package
    assert round_tripped.resolved_placement().placement is Placement.FEED


def test_publication_package_resolved_placement_uses_persisted_value_without_reinferring():
    # media[0] says jpeg (which would derive to FEED), but the persisted
    # placement explicitly says REEL — resolved_placement() must return the
    # persisted value, never re-derive from media_type.
    package = _make_package(
        placement=PublicationPlacement(
            channel=PublicationChannel.INSTAGRAM, placement=Placement.REEL, media_mode=MediaMode.VIDEO,
        ).to_dict(),
        media=[{"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    assert package.resolved_placement().placement is Placement.REEL


def test_publication_package_historical_v1_package_migrates_via_resolved_placement():
    # A schema v1 package (created before Milestone 11A) has no persisted
    # `placement` field at all — from_dict must still load it successfully
    # (backward compatible), and resolved_placement() must derive the
    # correct answer in-memory from channel + media_type.
    package = _make_package(schema_version=1, version=1, placement=None)
    data = package.to_dict()
    assert "version" in data and data["version"] == 1
    loaded = PublicationPackage.from_dict(data)
    assert loaded.placement is None
    assert loaded.resolved_placement().placement is Placement.FEED
    assert loaded.resolved_placement().media_mode is MediaMode.SINGLE_IMAGE


def test_publication_package_historical_v1_video_package_migrates_to_reel():
    package = _make_package(
        schema_version=1, version=1, placement=None,
        media=[{"asset_id": "a1", "media_type": "video/mp4", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    loaded = PublicationPackage.from_dict(package.to_dict())
    assert loaded.resolved_placement().placement is Placement.REEL


def test_publication_package_resolved_placement_never_writes_back_to_the_package():
    # Immutability: deriving a placement for a v1 package must never mutate
    # the (frozen) package object or its on-disk representation.
    package = _make_package(version=1, placement=None)
    before = package.to_dict()
    package.resolved_placement()
    package.resolved_placement()
    assert package.placement is None
    assert package.to_dict() == before


def test_publication_package_resolved_placement_raises_without_media_or_placement():
    package = _make_package(placement=None, media=[])
    with pytest.raises(UnsupportedPublicationMediaTypeError):
        package.resolved_placement()
