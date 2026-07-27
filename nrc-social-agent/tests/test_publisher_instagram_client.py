from unittest.mock import MagicMock, patch

import httpx

from src.publisher.instagram.client import HttpxMetaHttpClient, MetaHttpError


def _make_client():
    return HttpxMetaHttpClient(base_url="https://graph.instagram.com")


def _fake_response(status_code=200, json_body=None):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    return response


# --- debug_token (read-only) -------------------------------------------------


def test_debug_token_issues_a_get_request_with_input_token_param():
    client = _make_client()
    with patch("httpx.get", return_value=_fake_response(json_body={"data": {"is_valid": True}})) as mock_get:
        result = client.debug_token(
            input_token="tok-1", access_token="tok-1", api_version="v25.0", timeout_seconds=5.0
        )

    assert result == {"data": {"is_valid": True}}
    args, kwargs = mock_get.call_args
    assert args[0] == "https://graph.instagram.com/v25.0/debug_token"
    assert kwargs["params"]["input_token"] == "tok-1"
    assert kwargs["params"]["access_token"] == "tok-1"


def test_debug_token_raises_meta_http_error_on_error_response():
    client = _make_client()
    with patch("httpx.get", return_value=_fake_response(status_code=400, json_body={"error": {"code": 190}})):
        try:
            client.debug_token(input_token="bad", access_token="bad", api_version="v25.0", timeout_seconds=5.0)
            assert False, "expected MetaHttpError"
        except MetaHttpError as exc:
            assert exc.status_code == 400


# --- get_account_identity (read-only) ----------------------------------------


def test_get_account_identity_issues_a_get_request_for_id_and_username():
    client = _make_client()
    with patch("httpx.get", return_value=_fake_response(json_body={"id": "1", "username": "nrc_official"})) as mock_get:
        result = client.get_account_identity(
            instagram_account_id="ig-1", access_token="tok-1", api_version="v25.0", timeout_seconds=5.0
        )

    assert result == {"id": "1", "username": "nrc_official"}
    args, kwargs = mock_get.call_args
    assert args[0] == "https://graph.instagram.com/v25.0/ig-1"
    assert kwargs["params"]["fields"] == "id,username"


def test_get_account_identity_raises_meta_http_error_on_timeout():
    client = _make_client()
    with patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
        try:
            client.get_account_identity(
                instagram_account_id="ig-1", access_token="tok-1", api_version="v25.0", timeout_seconds=5.0
            )
            assert False, "expected MetaHttpError"
        except MetaHttpError as exc:
            assert exc.response_received is False


def test_neither_new_operation_is_a_write_operation():
    # Structural guarantee: both new methods only ever call httpx.get,
    # never httpx.post — grep-verifiable, but also asserted here so a
    # future edit that accidentally turns one into a POST fails a test.
    client = _make_client()
    with patch("httpx.post") as mock_post, patch("httpx.get", return_value=_fake_response(json_body={})):
        client.debug_token(input_token="t", access_token="t", api_version="v25.0", timeout_seconds=5.0)
        client.get_account_identity(instagram_account_id="ig-1", access_token="t", api_version="v25.0", timeout_seconds=5.0)
    mock_post.assert_not_called()
