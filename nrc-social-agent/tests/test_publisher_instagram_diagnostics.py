from unittest.mock import MagicMock

from src.publisher.instagram.client import MetaHttpError
from src.publisher.instagram.diagnostics import InstagramDiagnosticsResult, InstagramDiagnosticsService


def _make_service(http_client=None, **overrides):
    defaults = dict(
        http_client=http_client or MagicMock(),
        publishing_enabled=True,
        instagram_account_id="ig-account-1",
        access_token="token-abc",
        api_version="v25.0",
        request_timeout_seconds=5.0,
    )
    defaults.update(overrides)
    return InstagramDiagnosticsService(**defaults)


def _valid_debug_token_response(scopes=("instagram_business_basic", "instagram_business_content_publish")):
    return {"data": {"is_valid": True, "scopes": list(scopes)}}


# --- publishing disabled / not configured -----------------------------------


def test_check_status_reports_publishing_disabled_without_any_meta_call():
    http_client = MagicMock()
    service = _make_service(http_client=http_client, publishing_enabled=False)

    result = service.check_status()

    assert isinstance(result, InstagramDiagnosticsResult)
    assert result.application_healthy is True
    assert result.publishing_enabled is False
    assert result.instagram_ready is False
    assert result.failure_stage == "publishing_disabled"
    http_client.debug_token.assert_not_called()
    http_client.get_account_identity.assert_not_called()


def test_check_status_application_healthy_even_when_disabled():
    # Application health must never depend on Meta/Instagram configuration.
    service = _make_service(publishing_enabled=False, instagram_account_id=None, access_token=None)
    result = service.check_status()
    assert result.application_healthy is True


def test_check_status_reports_missing_configuration_without_any_meta_call():
    http_client = MagicMock()
    service = _make_service(http_client=http_client, publishing_enabled=True, instagram_account_id=None, access_token=None)

    result = service.check_status()

    assert result.instagram_configured is False
    assert result.instagram_ready is False
    assert result.failure_stage == "configuration"
    http_client.debug_token.assert_not_called()
    http_client.get_account_identity.assert_not_called()


# --- token validation --------------------------------------------------------


def test_check_status_reports_invalid_token_and_stops_before_account_check():
    http_client = MagicMock()
    http_client.debug_token.return_value = {"data": {"is_valid": False}}
    service = _make_service(http_client=http_client)

    result = service.check_status()

    assert result.instagram_configured is True
    assert result.token_valid is False
    assert result.instagram_ready is False
    assert result.failure_stage == "token_validation"
    http_client.get_account_identity.assert_not_called()


def test_check_status_treats_meta_http_error_on_token_check_as_invalid():
    http_client = MagicMock()
    http_client.debug_token.side_effect = MetaHttpError(
        "boom", status_code=401, error_body=None, response_received=True
    )
    service = _make_service(http_client=http_client)

    result = service.check_status()

    assert result.token_valid is False
    assert result.failure_stage == "token_validation"


# --- permission validation ----------------------------------------------------


def test_check_status_reports_missing_permissions_and_stops_before_account_check():
    http_client = MagicMock()
    http_client.debug_token.return_value = _valid_debug_token_response(scopes=("instagram_business_basic",))
    service = _make_service(http_client=http_client)

    result = service.check_status()

    assert result.token_valid is True
    assert result.permissions_ok is False
    assert result.instagram_ready is False
    assert result.failure_stage == "permission_validation"
    http_client.get_account_identity.assert_not_called()


# --- account verification ------------------------------------------------------


def test_check_status_reports_account_verification_failure():
    http_client = MagicMock()
    http_client.debug_token.return_value = _valid_debug_token_response()
    http_client.get_account_identity.side_effect = MetaHttpError(
        "boom", status_code=400, error_body=None, response_received=True
    )
    service = _make_service(http_client=http_client)

    result = service.check_status()

    assert result.permissions_ok is True
    assert result.account_verified is False
    assert result.instagram_ready is False
    assert result.failure_stage == "account_verification"


# --- fully ready ---------------------------------------------------------------


def test_check_status_reports_ready_when_every_stage_passes():
    http_client = MagicMock()
    http_client.debug_token.return_value = _valid_debug_token_response()
    http_client.get_account_identity.return_value = {"id": "179838...", "username": "nrc_official"}
    service = _make_service(http_client=http_client)

    result = service.check_status()

    assert result.application_healthy is True
    assert result.publishing_enabled is True
    assert result.instagram_configured is True
    assert result.token_valid is True
    assert result.permissions_ok is True
    assert result.account_verified is True
    assert result.account_username == "nrc_official"
    assert result.instagram_ready is True
    assert result.failure_stage is None
    assert result.failure_reason is None


# --- never mutates / never writes / never publishes ----------------------------


def test_check_status_never_calls_create_media_container_or_publish_media():
    http_client = MagicMock()
    http_client.debug_token.return_value = _valid_debug_token_response()
    http_client.get_account_identity.return_value = {"id": "1", "username": "nrc_official"}
    service = _make_service(http_client=http_client)

    service.check_status()

    http_client.create_media_container.assert_not_called()
    http_client.publish_media.assert_not_called()
    http_client.get_container_status.assert_not_called()


def test_check_status_never_raises_on_meta_failures():
    http_client = MagicMock()
    http_client.debug_token.side_effect = MetaHttpError("boom", status_code=500, error_body=None, response_received=False)
    service = _make_service(http_client=http_client)

    # Must not raise.
    result = service.check_status()
    assert result.instagram_ready is False
