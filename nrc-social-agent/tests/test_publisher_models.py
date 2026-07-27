from src.execution.models import DispatchCheckpoint
from src.publisher.models import FailureCategory, PublisherFailure, PublisherResult, PublisherStepResult


def test_publisher_result_to_dict_has_no_wider_fields():
    result = PublisherResult(
        platform="instagram", external_media_id="m1", external_container_id="c1", permalink=None,
        published_at="t1", verified_at="t1", media_type="IMAGE",
    )
    data = result.to_dict()
    assert set(data.keys()) == {
        "platform", "external_media_id", "external_container_id", "permalink", "published_at",
        "verified_at", "media_type",
    }


def test_publisher_failure_to_dict_round_trips_safely():
    failure = PublisherFailure(
        category=FailureCategory.RATE_LIMITED, retryable=True, operation="create_media_container",
        safe_message="Instagram is temporarily limiting requests.", occurred_at="t1",
        platform_code=4, platform_subcode=None, retry_after_seconds=300,
    )
    data = failure.to_dict()
    assert data["category"] == "RATE_LIMITED"
    assert data["retryable"] is True
    assert data["retry_after_seconds"] == 300


def test_publisher_step_result_is_terminal_only_with_result_or_failure():
    non_terminal = PublisherStepResult(next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED)
    assert non_terminal.is_terminal() is False

    with_result = PublisherStepResult(
        next_checkpoint=DispatchCheckpoint.VERIFIED,
        result=PublisherResult(
            platform="instagram", external_media_id="m1", external_container_id="c1", permalink=None,
            published_at="t1", verified_at="t1", media_type="IMAGE",
        ),
    )
    assert with_result.is_terminal() is True

    with_failure = PublisherStepResult(
        next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED,
        failure=PublisherFailure(
            category=FailureCategory.CONTAINER_REJECTED, retryable=False, operation="get_container_status",
            safe_message="failed", occurred_at="t1",
        ),
    )
    assert with_failure.is_terminal() is True
