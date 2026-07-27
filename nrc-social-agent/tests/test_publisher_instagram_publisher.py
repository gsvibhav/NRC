from unittest.mock import MagicMock

import pytest

from src.execution.models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publisher.errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from src.publisher.instagram.client import MetaHttpError
from src.publisher.instagram.media_access import TemporaryMediaSource
from src.publisher.instagram.publisher import InstagramPublisher


class _FakeClock:
    def now_iso(self):
        return "2026-01-01T00:00:00+00:00"


class _FakeMediaAccess:
    def __init__(self):
        self.calls = []

    def create_temporary_source(self, *, bucket, key, media_type, ttl_seconds):
        self.calls.append((bucket, key, media_type, ttl_seconds))
        return TemporaryMediaSource(url="https://example-presigned/x", expires_at="later", media_type=media_type)


def _make_package(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1, created_at="t", updated_at="t",
        content={"caption": "A great caption for this test.", "hashtags": ["nrc"], "cta": "Learn more"},
        media=[{"asset_id": "a1", "media_type": "image/jpeg", "storage_reference": {"bucket": "b", "key": "k"}}],
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


def _make_execution(**overrides) -> ExecutionDocument:
    defaults = dict(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM,
        status=ExecutionStatus.DISPATCH_IN_PROGRESS, attempt=1, publisher="instagram", created_at="t", updated_at="t",
    )
    defaults.update(overrides)
    return ExecutionDocument(**defaults)


def _make_publisher(http_client=None, media_access=None):
    return InstagramPublisher(
        http_client=http_client or MagicMock(), media_access=media_access or _FakeMediaAccess(), clock=_FakeClock(),
        instagram_account_id="ig-1", access_token="tok", api_version="v25.0",
        media_url_ttl_seconds=1800, request_timeout_seconds=10.0,
    )


# --- NOT_STARTED -> container creation -----------------------------------


def test_not_started_creates_container_and_advances_checkpoint():
    http_client = MagicMock()
    http_client.create_media_container.return_value = {"id": "container-1"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.NOT_STARTED)

    result = publisher.advance(execution, _make_package())

    assert result.next_checkpoint is DispatchCheckpoint.CONTAINER_CREATED
    assert result.platform_state_update["container_id"] == "container-1"
    _, kwargs = http_client.create_media_container.call_args
    assert kwargs["params"]["image_url"] == "https://example-presigned/x"
    assert "caption" in kwargs["params"]


def test_not_started_uses_video_url_and_reels_mode_for_mp4():
    http_client = MagicMock()
    http_client.create_media_container.return_value = {"id": "container-1"}
    publisher = _make_publisher(http_client=http_client)
    package = _make_package(media=[{"asset_id": "a1", "media_type": "video/mp4", "storage_reference": {"bucket": "b", "key": "k"}}])
    execution = _make_execution(checkpoint=DispatchCheckpoint.NOT_STARTED)

    publisher.advance(execution, package)

    _, kwargs = http_client.create_media_container.call_args
    assert kwargs["params"]["media_type"] == "REELS"
    assert kwargs["params"]["video_url"] == "https://example-presigned/x"
    assert "image_url" not in kwargs["params"]


def test_not_started_rejects_missing_credentials_without_calling_meta():
    http_client = MagicMock()
    publisher = InstagramPublisher(
        http_client=http_client, media_access=_FakeMediaAccess(), clock=_FakeClock(),
        instagram_account_id=None, access_token=None, api_version="v25.0",
        media_url_ttl_seconds=1800, request_timeout_seconds=10.0,
    )
    execution = _make_execution(checkpoint=DispatchCheckpoint.NOT_STARTED)

    with pytest.raises(PublisherPermanentError):
        publisher.advance(execution, _make_package())

    http_client.create_media_container.assert_not_called()


def test_not_started_ambiguous_container_creation_is_retryable_not_ambiguous():
    # Container-creation ambiguity is deliberately treated as ordinarily
    # retryable, not as a duplicate-publication risk.
    http_client = MagicMock()
    http_client.create_media_container.side_effect = MetaHttpError(
        "boom", status_code=None, error_body=None, response_received=False
    )
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.NOT_STARTED)

    with pytest.raises(PublisherRetryableError):
        publisher.advance(execution, _make_package())


def test_not_started_rejects_malformed_container_response():
    http_client = MagicMock()
    http_client.create_media_container.return_value = {}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.NOT_STARTED)

    with pytest.raises(PublisherPermanentError):
        publisher.advance(execution, _make_package())


# --- CONTAINER_CREATED / CONTAINER_PROCESSING -----------------------------


def test_container_created_finished_advances_to_container_ready():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "FINISHED"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state={"container_id": "c1"})

    result = publisher.advance(execution, _make_package())

    assert result.next_checkpoint is DispatchCheckpoint.CONTAINER_READY


def test_container_created_in_progress_advances_to_container_processing():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "IN_PROGRESS"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state={"container_id": "c1"})

    result = publisher.advance(execution, _make_package())

    assert result.next_checkpoint is DispatchCheckpoint.CONTAINER_PROCESSING


def test_container_error_is_permanent_and_resets_checkpoint():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "ERROR"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherPermanentError) as excinfo:
        publisher.advance(execution, _make_package())
    assert excinfo.value.reset_checkpoint is DispatchCheckpoint.NOT_STARTED


def test_container_expired_is_permanent_and_resets_checkpoint():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "EXPIRED"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherPermanentError) as excinfo:
        publisher.advance(execution, _make_package())
    assert excinfo.value.reset_checkpoint is DispatchCheckpoint.NOT_STARTED


def test_missing_container_id_is_permanent():
    publisher = _make_publisher()
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_CREATED, platform_state={})

    with pytest.raises(PublisherPermanentError):
        publisher.advance(execution, _make_package())


# --- CONTAINER_READY -> publish -------------------------------------------


def test_container_ready_publishes_and_returns_result():
    http_client = MagicMock()
    http_client.publish_media.return_value = {"id": "media-123"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_READY, platform_state={"container_id": "c1"})

    result = publisher.advance(execution, _make_package())

    assert result.next_checkpoint is DispatchCheckpoint.VERIFIED
    assert result.result.external_media_id == "media-123"
    assert result.result.external_container_id == "c1"


def test_container_ready_ambiguous_publish_raises_ambiguous_with_container_id():
    http_client = MagicMock()
    http_client.publish_media.side_effect = MetaHttpError(
        "boom", status_code=None, error_body=None, response_received=False
    )
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_READY, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherAmbiguousError) as excinfo:
        publisher.advance(execution, _make_package())
    assert excinfo.value.partial_platform_state == {"container_id": "c1"}


def test_container_ready_missing_media_id_in_response_is_ambiguous():
    http_client = MagicMock()
    http_client.publish_media.return_value = {}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.CONTAINER_READY, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherAmbiguousError):
        publisher.advance(execution, _make_package())


# --- PUBLISH_REQUESTED: reconciliation only -------------------------------


def test_publish_requested_reconciles_published_status_to_success_without_republishing():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "PUBLISHED"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED, platform_state={"container_id": "c1"})

    result = publisher.advance(execution, _make_package())

    assert result.next_checkpoint is DispatchCheckpoint.VERIFIED
    assert result.result.external_media_id is None  # documented limitation
    http_client.publish_media.assert_not_called()


def test_publish_requested_reconciles_error_to_permanent_failure_and_resets_checkpoint():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "ERROR"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherPermanentError) as excinfo:
        publisher.advance(execution, _make_package())
    assert excinfo.value.reset_checkpoint is DispatchCheckpoint.NOT_STARTED
    http_client.publish_media.assert_not_called()


def test_publish_requested_still_ambiguous_requires_operator_intervention():
    http_client = MagicMock()
    http_client.get_container_status.return_value = {"status_code": "FINISHED"}
    publisher = _make_publisher(http_client=http_client)
    execution = _make_execution(checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED, platform_state={"container_id": "c1"})

    with pytest.raises(PublisherPermanentError) as excinfo:
        publisher.advance(execution, _make_package())
    # Checkpoint is NOT reset — preserved for operator investigation.
    assert excinfo.value.reset_checkpoint is None
    assert excinfo.value.failure.category.value == "AMBIGUOUS_PUBLISH_OUTCOME"
    assert excinfo.value.failure.retryable is False
    http_client.publish_media.assert_not_called()


def test_publish_requested_never_calls_publish_media_regardless_of_outcome():
    for status_code in ("PUBLISHED", "ERROR", "EXPIRED", "FINISHED", "IN_PROGRESS"):
        http_client = MagicMock()
        http_client.get_container_status.return_value = {"status_code": status_code}
        publisher = _make_publisher(http_client=http_client)
        execution = _make_execution(checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED, platform_state={"container_id": "c1"})
        try:
            publisher.advance(execution, _make_package())
        except PublisherPermanentError:
            pass
        http_client.publish_media.assert_not_called()
