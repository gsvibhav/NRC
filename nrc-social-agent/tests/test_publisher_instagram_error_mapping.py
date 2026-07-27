from src.publisher.errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from src.publisher.instagram.client import MetaHttpError
from src.publisher.instagram.error_mapping import map_meta_http_error


def _error(*, status_code, error_body, response_received=True):
    return MetaHttpError("boom", status_code=status_code, error_body=error_body, response_received=response_received)


def test_no_response_received_on_non_irreversible_call_is_retryable():
    exc = _error(status_code=None, error_body=None, response_received=False)
    result = map_meta_http_error(exc, operation="create_media_container", is_irreversible=False)
    assert isinstance(result, PublisherRetryableError)


def test_no_response_received_on_irreversible_call_is_ambiguous():
    exc = _error(status_code=None, error_body=None, response_received=False)
    result = map_meta_http_error(
        exc, operation="publish_media", is_irreversible=True, partial_platform_state={"container_id": "c1"}
    )
    assert isinstance(result, PublisherAmbiguousError)
    assert result.partial_platform_state == {"container_id": "c1"}


def test_rate_limit_code_4_is_retryable():
    exc = _error(status_code=400, error_body={"error": {"code": 4, "message": "throttled"}})
    result = map_meta_http_error(exc, operation="create_media_container", is_irreversible=False)
    assert isinstance(result, PublisherRetryableError)
    assert result.failure.retryable is True
    assert result.failure.category.value == "RATE_LIMITED"


def test_code_190_expired_token_is_permanent():
    exc = _error(status_code=401, error_body={"error": {"code": 190, "message": "expired"}})
    result = map_meta_http_error(exc, operation="publish_media", is_irreversible=True)
    assert isinstance(result, PublisherPermanentError)
    assert result.failure.retryable is False
    assert result.failure.category.value == "INVALID_CREDENTIALS"


def test_code_10_permission_denied_is_permanent():
    exc = _error(status_code=403, error_body={"error": {"code": 10, "message": "denied"}})
    result = map_meta_http_error(exc, operation="create_media_container", is_irreversible=False)
    assert isinstance(result, PublisherPermanentError)
    assert result.failure.category.value == "MISSING_PERMISSION"


def test_definitive_error_response_to_publish_media_is_not_ambiguous():
    # Even though publish_media is irreversible, a *real* error response
    # (Meta was reached and gave a definitive answer) is classified
    # normally, never as ambiguous.
    exc = _error(status_code=400, error_body={"error": {"code": 100, "message": "invalid param"}})
    result = map_meta_http_error(exc, operation="publish_media", is_irreversible=True)
    assert isinstance(result, PublisherPermanentError)
    assert not isinstance(result, PublisherAmbiguousError)


def test_unparseable_body_on_irreversible_call_is_ambiguous():
    exc = _error(status_code=200, error_body=None, response_received=True)
    result = map_meta_http_error(
        exc, operation="publish_media", is_irreversible=True, partial_platform_state={"container_id": "c1"}
    )
    assert isinstance(result, PublisherAmbiguousError)


def test_retry_after_seconds_extracted_when_present():
    exc = _error(
        status_code=400,
        error_body={"error": {"code": 4, "error_data": {"retry_after_seconds": 120}}},
    )
    result = map_meta_http_error(exc, operation="create_media_container", is_irreversible=False)
    assert result.failure.retry_after_seconds == 120
