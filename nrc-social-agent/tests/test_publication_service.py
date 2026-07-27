from unittest.mock import MagicMock

import pytest

from src.ai.content_plan_models import ContentPlanDocument, ContentPlanStatus, PlanStrategy, PlannedOutput
from src.ai.draft_models import DraftDocument, DraftStatus
from src.ai.draft_version_models import CurrentDraftPointer, ReviewStatus
from src.publication.errors import (
    ApprovedVersionLinkageMismatchError,
    MissingApprovalMetadataForPublicationError,
    MissingContentPlanForPublicationError,
    PublicationConcurrentModificationError,
    PublicationValidationFailedError,
    UnsupportedPublicationOutputTypeError,
    WorkflowNotEligibleForPublicationError,
)
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publication.service import PublicationOutcome, PublicationPreparationService
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

VALID_STRATEGY = PlanStrategy(
    content_type="founder_introduction",
    primary_objective="build founder credibility for NRC",
    central_message="premium credibility",
    brand_positioning="premium, connected",
    cta_direction="invite viewers to explore NRC",
    audience=["business owners"],
    tone_direction=["confident"],
    factual_constraints=["brand: NRC"],
    avoid=["performance claims"],
)


def _output(**overrides):
    defaults = dict(
        output_id="out-1", output_type="instagram_reel_caption", priority=1,
        purpose="build founder credibility on Instagram",
        message_focus="make the founder story memorable",
        cta_direction="invite viewers to explore NRC", audience=["business owners"], tone=["confident"],
    )
    defaults.update(overrides)
    return PlannedOutput(**defaults)


def _make_plan(**overrides):
    defaults = dict(
        plan_id="plan-1", workflow_id="wf-1", created_at="x", updated_at="x", status=ContentPlanStatus.COMPLETED,
        schema_version=1, prompt_version=1, model="claude-opus-5", strategy=VALID_STRATEGY, outputs=[_output()],
    )
    defaults.update(overrides)
    return ContentPlanDocument(**defaults)


def _make_current_document(**overrides):
    defaults = dict(
        draft_id="draft-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", created_at="x", updated_at="x",
        status=DraftStatus.READY_FOR_REVIEW, schema_version=2, prompt_version=1, model="claude-opus-5",
        content={"caption": "A great, meaningfully long approved caption about our brand.", "hashtags": ["nrc"], "cta": "Learn more"},
        version_number=1,
    )
    defaults.update(overrides)
    return DraftDocument(**defaults)


def _make_current_pointer(**overrides):
    defaults = dict(
        workflow_id="wf-1", output_id="out-1", current_draft_id="draft-1", current_version_number=1,
        status=ReviewStatus.APPROVED, updated_at="x",
        approved_at="t0", approved_by_telegram_user_id=42,
    )
    defaults.update(overrides)
    return CurrentDraftPointer(**defaults)


def _make_workflow(**overrides):
    defaults = dict(
        workflow_id="wf-1", telegram_user_id=42, created_at="x", updated_at="x",
        state=WorkflowState.COMPLETED,
        media={"telegram_file_unique_id": "tg-1", "mime_type": "image/jpeg", "s3_key": "media/wf-1/photo.jpg"},
        metadata={},
        generated_draft={
            "draft_id": "draft-1", "output_id": "out-1", "output_type": "instagram_reel_caption",
            "current_version": 1, "status": "APPROVED", "schema_version": 2,
            "approved_at": "t0", "approved_by_telegram_user_id": 42,
        },
        publication=None,
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_service(
    *, workflow_manager=None, draft_manager=None, draft_version_manager=None,
    content_plan_manager=None, publication_manager=None, conversation_manager=None,
    max_instagram_caption_length=2200, max_hashtags=30,
):
    return PublicationPreparationService(
        schema_version=1,
        max_instagram_caption_length=max_instagram_caption_length,
        max_hashtags=max_hashtags,
        s3_bucket_name="my-bucket",
        workflow_manager=workflow_manager or MagicMock(),
        draft_manager=draft_manager or MagicMock(),
        draft_version_manager=draft_version_manager or MagicMock(),
        content_plan_manager=content_plan_manager or MagicMock(),
        publication_manager=publication_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


def _stub_current_draft(draft_version_manager, *, document=None, pointer=None, etag='"p-etag"'):
    draft_version_manager.get_current.return_value = (
        document or _make_current_document(), pointer or _make_current_pointer(), etag,
    )


# --- success path ------------------------------------------------------


async def test_prepare_publication_builds_and_persists_a_new_package():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    created = MagicMock(spec=PublicationPackage)
    created.publication_id = "pub-1"
    created.output_id = "out-1"
    created.draft = {"draft_id": "draft-1", "version_number": 1}
    created.channel = PublicationChannel.INSTAGRAM
    created.status = PublicationStatus.READY_FOR_PUBLISHING
    created.schema_version = 1
    created.updated_at = "t2"
    created.output_type = "instagram_reel_caption"
    created.workflow_id = "wf-1"
    publication_manager.create_publication.return_value = created
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
        conversation_manager=conversation_manager,
    )

    outcome = await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    assert isinstance(outcome, PublicationOutcome)
    assert outcome.publication_id == "pub-1"
    assert outcome.status == "READY_FOR_PUBLISHING"

    publication_manager.create_publication.assert_called_once()
    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["output_type"] == "instagram_reel_caption"
    assert kwargs["channel"] is PublicationChannel.INSTAGRAM
    assert kwargs["content"]["caption"] == _make_current_document().content["caption"]
    assert kwargs["media"][0]["storage_reference"] == {"bucket": "my-bucket", "key": "media/wf-1/photo.jpg"}
    assert kwargs["draft"] == {"draft_id": "draft-1", "version_number": 1}
    assert kwargs["approval"] == {"approved_at": "t0", "approved_by_telegram_user_id": 42}

    workflow_manager.attach_publication_reference.assert_called_once()
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_prepare_publication_persists_feed_placement_for_jpeg_media():
    # Milestone 11A: the media in _make_workflow() is image/jpeg — the
    # resolved placement persisted alongside the package must be FEED.
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    created = MagicMock(spec=PublicationPackage)
    created.publication_id = "pub-1"
    created.output_id = "out-1"
    created.channel = PublicationChannel.INSTAGRAM
    created.status = PublicationStatus.READY_FOR_PUBLISHING
    created.schema_version = 1
    created.updated_at = "t2"
    created.output_type = "instagram_reel_caption"
    created.workflow_id = "wf-1"
    publication_manager.create_publication.return_value = created

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["placement"] == {"channel": "instagram", "placement": "feed", "media_mode": "single_image"}


async def test_prepare_publication_persists_reel_placement_for_mp4_media():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(
        media={"telegram_file_unique_id": "tg-1", "mime_type": "video/mp4", "s3_key": "media/wf-1/clip.mp4"},
    )
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    created = MagicMock(spec=PublicationPackage)
    created.publication_id = "pub-1"
    created.output_id = "out-1"
    created.channel = PublicationChannel.INSTAGRAM
    created.status = PublicationStatus.READY_FOR_PUBLISHING
    created.schema_version = 1
    created.updated_at = "t2"
    created.output_type = "instagram_reel_caption"
    created.workflow_id = "wf-1"
    publication_manager.create_publication.return_value = created

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["placement"] == {"channel": "instagram", "placement": "reel", "media_mode": "video"}


async def test_prepare_publication_never_calls_claude():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    # No collaborator passed to this service exposes a Claude client at
    # all — the strongest possible guarantee that no Claude call occurred.
    assert not hasattr(service, "_claude_client")


async def test_prepare_publication_content_is_unchanged_apart_from_mechanical_normalization():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(
        draft_version_manager,
        document=_make_current_document(content={"caption": "  Exact approved wording.  ", "hashtags": ["#nrc"], "cta": "Learn more"}),
    )
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["content"]["caption"] == "Exact approved wording."
    assert kwargs["content"]["hashtags"] == ["nrc"]


# --- idempotency ---------------------------------------------------------


async def test_prepare_publication_reuses_existing_ready_package():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    publication_manager = MagicMock()
    existing = MagicMock(spec=PublicationPackage)
    existing.publication_id = "pub-existing"
    existing.output_id = "out-1"
    existing.draft = {"draft_id": "draft-1", "version_number": 1}
    existing.channel = PublicationChannel.INSTAGRAM
    existing.status = PublicationStatus.READY_FOR_PUBLISHING
    existing.schema_version = 1
    existing.updated_at = "t1"
    existing.output_type = "instagram_reel_caption"
    existing.workflow_id = "wf-1"
    publication_manager.find_existing.return_value = existing
    draft_version_manager = MagicMock()
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        draft_version_manager=draft_version_manager, conversation_manager=conversation_manager,
    )

    outcome = await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.publication_id == "pub-existing"
    publication_manager.create_publication.assert_not_called()
    publication_manager.supersede_failed_publication.assert_not_called()
    draft_version_manager.get_current.assert_not_called()
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_prepare_publication_supersedes_an_existing_failed_package():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    failed = MagicMock(spec=PublicationPackage)
    failed.status = PublicationStatus.FAILED
    publication_manager.find_existing.return_value = failed
    superseded = MagicMock(spec=PublicationPackage)
    superseded.publication_id = "pub-1"
    superseded.workflow_id = "wf-1"
    superseded.output_id = "out-1"
    superseded.draft = {"draft_id": "draft-1", "version_number": 1}
    superseded.channel = PublicationChannel.INSTAGRAM
    superseded.status = PublicationStatus.READY_FOR_PUBLISHING
    superseded.schema_version = 1
    superseded.updated_at = "t2"
    superseded.output_type = "instagram_reel_caption"
    publication_manager.supersede_failed_publication.return_value = superseded

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    outcome = await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.publication_id == "pub-1"
    publication_manager.supersede_failed_publication.assert_called_once()
    publication_manager.create_publication.assert_not_called()


# --- eligibility -----------------------------------------------------------


async def test_prepare_publication_rejects_workflow_not_completed():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow(state=WorkflowState.SHOWING_PREVIEW)
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(WorkflowNotEligibleForPublicationError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)


async def test_prepare_publication_rejects_workflow_not_approved():
    workflow_manager = MagicMock()
    workflow = _make_workflow(generated_draft={
        "draft_id": "draft-1", "output_id": "out-1", "output_type": "instagram_reel_caption",
        "current_version": 1, "status": "SAVED_AS_DRAFT",
    })
    workflow_manager.load_workflow.return_value = workflow
    service = _make_service(workflow_manager=workflow_manager)

    with pytest.raises(WorkflowNotEligibleForPublicationError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)


async def test_prepare_publication_rejects_missing_approval_metadata():
    workflow_manager = MagicMock()
    workflow = _make_workflow(generated_draft={
        "draft_id": "draft-1", "output_id": "out-1", "output_type": "instagram_reel_caption",
        "current_version": 1, "status": "APPROVED",
        # approved_at / approved_by_telegram_user_id missing
    })
    workflow_manager.load_workflow.return_value = workflow
    publication_manager = MagicMock()
    service = _make_service(workflow_manager=workflow_manager, publication_manager=publication_manager)

    with pytest.raises(MissingApprovalMetadataForPublicationError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    publication_manager.find_existing.assert_not_called()


async def test_prepare_publication_rejects_unsupported_output_type_without_touching_draft_or_plan():
    workflow_manager = MagicMock()
    workflow = _make_workflow(generated_draft={
        "draft_id": "draft-1", "output_id": "out-1", "output_type": "linkedin_post",
        "current_version": 1, "status": "APPROVED",
        "approved_at": "t0", "approved_by_telegram_user_id": 42,
    })
    workflow_manager.load_workflow.return_value = workflow
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    failed_record = MagicMock(spec=PublicationPackage)
    failed_record.publication_id = "pub-failed"
    failed_record.status = PublicationStatus.FAILED
    failed_record.schema_version = 1
    failed_record.updated_at = "t1"
    publication_manager.create_publication.return_value = failed_record
    draft_version_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, publication_manager=publication_manager,
        draft_version_manager=draft_version_manager,
    )

    with pytest.raises(UnsupportedPublicationOutputTypeError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    # resolve_channel() fails before the current draft version or content
    # plan are ever loaded.
    draft_version_manager.get_current.assert_not_called()

    # A best-effort FAILED record is still persisted for audit, using a
    # placeholder channel (never read as if it were real — nothing
    # publishes from a FAILED record).
    publication_manager.create_publication.assert_called_once()
    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["status"] is PublicationStatus.FAILED

    workflow_manager.attach_publication_reference.assert_called_once()
    args, _ = workflow_manager.attach_publication_reference.call_args
    assert args[1]["status"] == "FAILED"


async def test_prepare_publication_rejects_when_approved_version_does_not_match_current_pointer():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager, pointer=_make_current_pointer(current_version_number=2))
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        publication_manager=publication_manager,
    )

    with pytest.raises(ApprovedVersionLinkageMismatchError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)


async def test_prepare_publication_rejects_missing_content_plan():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = None
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    with pytest.raises(MissingContentPlanForPublicationError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)


async def test_prepare_publication_only_ever_creates_one_output_package_even_with_multiple_planned_outputs():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan(
        outputs=[_output(output_id="out-1"), _output(output_id="out-2", output_type="linkedin_post", priority=2)]
    )
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
    )

    await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    assert publication_manager.create_publication.call_count == 1
    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["output_id"] == "out-1"


# --- validation failure ----------------------------------------------------


async def test_prepare_publication_permanent_failure_on_validation_records_failed_package():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager, document=_make_current_document(content={"caption": "Hi", "hashtags": [], "cta": None}))
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    failed_record = MagicMock(spec=PublicationPackage)
    failed_record.publication_id = "pub-failed"
    failed_record.status = PublicationStatus.FAILED
    failed_record.schema_version = 1
    failed_record.updated_at = "t1"
    publication_manager.create_publication.return_value = failed_record
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(PublicationValidationFailedError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    # A FAILED record is persisted for audit...
    assert publication_manager.create_publication.call_count == 1
    _, kwargs = publication_manager.create_publication.call_args
    assert kwargs["status"] is PublicationStatus.FAILED

    # ...pending_retry is cleared, but the active pointer is NOT cleared
    # (the approved workflow remains valid and /status must still be able
    # to describe it).
    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": None})
    conversation_manager.clear_active_workflow.assert_not_called()


# --- retryable persistence failure ---------------------------------------


async def test_prepare_publication_sets_pending_retry_on_concurrent_modification():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow()
    draft_version_manager = MagicMock()
    _stub_current_draft(draft_version_manager)
    content_plan_manager = MagicMock()
    content_plan_manager.find_existing.return_value = _make_plan()
    publication_manager = MagicMock()
    publication_manager.find_existing.return_value = None
    publication_manager.create_publication.side_effect = PublicationConcurrentModificationError("conflict")
    conversation_manager = MagicMock()

    service = _make_service(
        workflow_manager=workflow_manager, draft_version_manager=draft_version_manager,
        content_plan_manager=content_plan_manager, publication_manager=publication_manager,
        conversation_manager=conversation_manager,
    )

    with pytest.raises(PublicationConcurrentModificationError):
        await service.prepare_publication(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "publication_preparation"})
    conversation_manager.clear_active_workflow.assert_not_called()
    workflow_manager.attach_publication_reference.assert_not_called()
