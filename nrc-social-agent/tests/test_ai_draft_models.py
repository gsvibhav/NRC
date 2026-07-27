import pytest

from src.ai.draft_models import (
    CONTENT_MODELS_BY_OUTPUT_TYPE,
    CURRENT_DOCUMENT_VERSION,
    SUPPORTED_DOCUMENT_VERSIONS,
    DraftDocument,
    DraftSourceVersions,
    DraftStatus,
    DraftUsage,
    InstagramReelCaptionContent,
)
from src.ai.errors import (
    DraftResponseSchemaMismatchError,
    DraftVersionMismatchError,
    InvalidDraftStatusError,
)


def _make_content(**overrides):
    defaults = dict(caption="A great caption about NRC's founder.")
    defaults.update(overrides)
    return InstagramReelCaptionContent(**defaults)


def _make_document(**overrides):
    defaults = dict(
        draft_id="draft-1",
        workflow_id="wf-1",
        plan_id="plan-1",
        output_id="out-1",
        output_type="instagram_reel_caption",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=DraftStatus.READY_FOR_REVIEW,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        content=_make_content().to_dict(),
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


# --- InstagramReelCaptionContent ------------------------------------------


def test_instagram_reel_caption_content_defaults_hashtags_and_cta():
    content = InstagramReelCaptionContent(caption="Hello world.")

    assert content.hashtags == []
    assert content.cta is None


def test_instagram_reel_caption_content_round_trips_through_dict():
    content = _make_content(hashtags=["nrc", "branding"], cta="Explore NRC's work.")

    assert InstagramReelCaptionContent.from_dict(content.to_dict()) == content


def test_instagram_reel_caption_content_from_dict_rejects_non_dict():
    with pytest.raises(DraftResponseSchemaMismatchError):
        InstagramReelCaptionContent.from_dict("not a dict")


def test_instagram_reel_caption_content_from_dict_rejects_missing_caption():
    with pytest.raises(DraftResponseSchemaMismatchError):
        InstagramReelCaptionContent.from_dict({"hashtags": [], "cta": None})


def test_instagram_reel_caption_content_from_dict_rejects_empty_caption():
    with pytest.raises(DraftResponseSchemaMismatchError):
        InstagramReelCaptionContent.from_dict({"caption": "", "hashtags": [], "cta": None})


def test_instagram_reel_caption_content_from_dict_rejects_non_string_hashtags():
    with pytest.raises(DraftResponseSchemaMismatchError):
        InstagramReelCaptionContent.from_dict({"caption": "hi", "hashtags": [1, 2], "cta": None})


def test_instagram_reel_caption_content_from_dict_treats_empty_string_cta_as_none():
    content = InstagramReelCaptionContent.from_dict({"caption": "hi", "hashtags": [], "cta": ""})

    assert content.cta is None


def test_instagram_reel_caption_content_from_dict_rejects_non_string_cta():
    with pytest.raises(DraftResponseSchemaMismatchError):
        InstagramReelCaptionContent.from_dict({"caption": "hi", "hashtags": [], "cta": 5})


def test_content_models_by_output_type_covers_only_instagram_reel_caption():
    assert CONTENT_MODELS_BY_OUTPUT_TYPE == {"instagram_reel_caption": InstagramReelCaptionContent}


# --- DraftSourceVersions ---------------------------------------------------


def test_draft_source_versions_round_trips_through_dict():
    versions = DraftSourceVersions(
        analysis_schema_version=1, content_plan_schema_version=1, draft_prompt_version=1,
        clarification_context_version=2,
    )

    assert DraftSourceVersions.from_dict(versions.to_dict()) == versions


def test_draft_source_versions_allows_none_clarification_context_version():
    versions = DraftSourceVersions(
        analysis_schema_version=1, content_plan_schema_version=1, draft_prompt_version=1,
    )

    assert DraftSourceVersions.from_dict(versions.to_dict()).clarification_context_version is None


def test_draft_source_versions_from_dict_rejects_non_int_field():
    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftSourceVersions.from_dict(
            {"analysis_schema_version": "1", "content_plan_schema_version": 1, "draft_prompt_version": 1}
        )


# --- DraftUsage -------------------------------------------------------------


def test_draft_usage_round_trips_through_dict():
    usage = DraftUsage(input_tokens=10, output_tokens=5)

    assert DraftUsage.from_dict(usage.to_dict()) == usage


def test_draft_usage_from_dict_rejects_bool_as_int():
    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftUsage.from_dict({"input_tokens": True, "output_tokens": 5})


# --- DraftDocument ----------------------------------------------------------


def test_draft_document_round_trips_through_dict():
    document = _make_document()

    assert DraftDocument.from_dict(document.to_dict()) == document


def test_draft_document_defaults_version_to_current():
    document = _make_document()

    assert document.version == CURRENT_DOCUMENT_VERSION


def test_draft_document_from_dict_rejects_unsupported_version():
    data = _make_document().to_dict()
    data["version"] = 999

    with pytest.raises(DraftVersionMismatchError):
        DraftDocument.from_dict(data)

    assert 999 not in SUPPORTED_DOCUMENT_VERSIONS


def test_draft_document_from_dict_rejects_missing_required_field():
    data = _make_document().to_dict()
    del data["draft_id"]

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


def test_draft_document_from_dict_rejects_unrecognized_status():
    data = _make_document().to_dict()
    data["status"] = "SOMETHING_ELSE"

    with pytest.raises(InvalidDraftStatusError):
        DraftDocument.from_dict(data)


def test_draft_document_allows_none_content_for_a_failed_draft():
    document = _make_document(status=DraftStatus.FAILED, content=None)

    restored = DraftDocument.from_dict(document.to_dict())

    assert restored.content is None
    assert restored.status is DraftStatus.FAILED


def test_draft_document_from_dict_rejects_non_dict_content():
    data = _make_document().to_dict()
    data["content"] = "not a dict"

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


def test_draft_document_from_dict_rejects_non_dict_metadata():
    data = _make_document().to_dict()
    data["metadata"] = "not a dict"

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


# --- Milestone 7: version_number / parent_version_number / edit_instruction_reference ---


def test_draft_document_defaults_to_version_number_1_with_no_parent():
    document = _make_document()

    assert document.version_number == 1
    assert document.parent_version_number is None
    assert document.edit_instruction_reference is None


def test_draft_document_round_trips_version_fields():
    document = _make_document(
        version_number=2, parent_version_number=1,
        edit_instruction_reference={"instruction_id": "op-1", "created_at": "x"},
    )

    restored = DraftDocument.from_dict(document.to_dict())

    assert restored.version_number == 2
    assert restored.parent_version_number == 1
    assert restored.edit_instruction_reference == {"instruction_id": "op-1", "created_at": "x"}


def test_draft_document_reads_a_version_1_document_as_version_number_1():
    # A genuine Milestone-6-era document has no version_number/
    # parent_version_number/edit_instruction_reference fields at all —
    # from_dict() must default them sensibly (a flat draft is always,
    # by definition, its own first and only version).
    data = _make_document().to_dict()
    data["version"] = 1
    del data["version_number"]
    del data["parent_version_number"]
    del data["edit_instruction_reference"]

    document = DraftDocument.from_dict(data)

    assert document.version == 1
    assert document.version_number == 1
    assert document.parent_version_number is None
    assert document.edit_instruction_reference is None


def test_draft_document_from_dict_rejects_non_int_version_number():
    data = _make_document().to_dict()
    data["version_number"] = "1"

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


def test_draft_document_from_dict_rejects_version_number_below_one():
    data = _make_document().to_dict()
    data["version_number"] = 0

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


def test_draft_document_from_dict_rejects_non_dict_edit_instruction_reference():
    data = _make_document().to_dict()
    data["edit_instruction_reference"] = "not a dict"

    with pytest.raises(DraftResponseSchemaMismatchError):
        DraftDocument.from_dict(data)


def test_current_document_version_and_supported_versions_include_2():
    assert CURRENT_DOCUMENT_VERSION == 2
    assert SUPPORTED_DOCUMENT_VERSIONS == {1, 2}
