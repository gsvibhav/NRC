"""Narrow, injectable HTTP boundary for the three Meta Graph API
operations Instagram Content Publishing needs. Explicit methods, not a
generic `request()` — see this module's own reasoning below.

**Selected authentication model: Instagram API with Instagram Login
(Business Login).** Chosen over "Instagram API with Facebook Login for
Business" because it requires only a standalone Instagram Business/
Creator account (no linked Facebook Page), which matches NRC's actual
account setup, and uses the base host `graph.instagram.com` directly.
Facebook Login's resumable chunked-upload path (`rupload.facebook.com`)
is not needed here: both image and Reel/video publishing use the
public-URL parameter (`image_url`/`video_url`) form, which Instagram
Login fully supports.

**Selected Graph API version:** the config-supplied `api_version`
(default `v25.0` — the current version per Meta's own versions page as
of this milestone; see the completion report for the exact date
verified) is passed on every call, never hardcoded into a request path
in more than one place.

**Never automatically retried around irreversible operations**: this
client issues exactly one HTTP request per method call and lets the
caller (the Instagram publisher / dispatch service) decide what to do
with a failure — an HTTP library's own automatic-retry behavior must
never silently repeat a `publish_media()` POST.

**Milestone 11A adds two read-only operations** — `debug_token()` and
`get_account_identity()` — used only by
`src/publisher/instagram/diagnostics.py`'s `/instagram-status` command.
Both are plain `GET` requests; neither creates, modifies, or publishes
anything, so they carry none of the "never automatically retried"
concern above (a repeated GET is harmless). `debug_token()` maps to
Meta's standard token-introspection endpoint (`GET /debug_token`,
returning `data.is_valid`/`data.scopes`/`data.expires_at`); the
configured access token is passed as both `input_token` and
`access_token` for self-inspection, since this repository does not hold
a separate app access token. `get_account_identity()` fetches the
configured Instagram account's own `id`/`username` — the minimum needed
to confirm the credentials resolve to a real, reachable account.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://graph.instagram.com"


class MetaHttpError(Exception):
    """Raised for any transport-level failure (connection error, timeout,
    non-2xx response, or an unparseable body) — src/publisher/instagram/
    error_mapping.py is the only place that interprets these into
    retryable/permanent/ambiguous. Carries the parsed error body (if any)
    and whether a response was ever received at all, but never the
    access token or full request URL."""

    def __init__(self, message: str, *, status_code: int | None, error_body: dict | None, response_received: bool):
        super().__init__(message)
        self.status_code = status_code
        self.error_body = error_body or {}
        self.response_received = response_received


class MetaHttpClient(Protocol):
    def create_media_container(
        self, *, instagram_account_id: str, params: dict, access_token: str, api_version: str, timeout_seconds: float
    ) -> dict: ...

    def get_container_status(
        self, *, container_id: str, access_token: str, api_version: str, timeout_seconds: float
    ) -> dict: ...

    def publish_media(
        self, *, instagram_account_id: str, container_id: str, access_token: str, api_version: str, timeout_seconds: float
    ) -> dict: ...

    def debug_token(
        self, *, input_token: str, access_token: str, api_version: str, timeout_seconds: float
    ) -> dict: ...

    def get_account_identity(
        self, *, instagram_account_id: str, access_token: str, api_version: str, timeout_seconds: float
    ) -> dict: ...


class HttpxMetaHttpClient:
    """The real, network-calling implementation — constructed only in
    main.py, never in a test. `params` for container creation is built
    entirely by the Instagram adapter (image_url/video_url/media_type/
    caption); this client only adds `access_token` and issues the
    request."""

    def __init__(self, *, base_url: str = _BASE_URL) -> None:
        self._base_url = base_url

    def _post(self, path: str, *, params: dict, access_token: str, timeout_seconds: float) -> dict:
        url = f"{self._base_url}/{path}"
        try:
            response = httpx.post(url, data={**params, "access_token": access_token}, timeout=timeout_seconds)
        except httpx.TimeoutException as exc:
            raise MetaHttpError("request timed out", status_code=None, error_body=None, response_received=False) from exc
        except httpx.TransportError as exc:
            raise MetaHttpError("connection failed", status_code=None, error_body=None, response_received=False) from exc
        return self._parse(response)

    def _get(self, path: str, *, params: dict, access_token: str, timeout_seconds: float) -> dict:
        url = f"{self._base_url}/{path}"
        try:
            response = httpx.get(url, params={**params, "access_token": access_token}, timeout=timeout_seconds)
        except httpx.TimeoutException as exc:
            raise MetaHttpError("request timed out", status_code=None, error_body=None, response_received=False) from exc
        except httpx.TransportError as exc:
            raise MetaHttpError("connection failed", status_code=None, error_body=None, response_received=False) from exc
        return self._parse(response)

    @staticmethod
    def _parse(response: "httpx.Response") -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise MetaHttpError(
                "response body was not valid JSON", status_code=response.status_code, error_body=None,
                response_received=True,
            ) from exc

        if response.status_code >= 400:
            raise MetaHttpError(
                "Meta returned an error response", status_code=response.status_code,
                error_body=body if isinstance(body, dict) else None, response_received=True,
            )
        if not isinstance(body, dict):
            raise MetaHttpError(
                "expected a JSON object response", status_code=response.status_code, error_body=None,
                response_received=True,
            )
        return body

    def create_media_container(self, *, instagram_account_id, params, access_token, api_version, timeout_seconds) -> dict:
        return self._post(
            f"{api_version}/{instagram_account_id}/media", params=params, access_token=access_token,
            timeout_seconds=timeout_seconds,
        )

    def get_container_status(self, *, container_id, access_token, api_version, timeout_seconds) -> dict:
        return self._get(
            f"{api_version}/{container_id}", params={"fields": "status_code"}, access_token=access_token,
            timeout_seconds=timeout_seconds,
        )

    def publish_media(self, *, instagram_account_id, container_id, access_token, api_version, timeout_seconds) -> dict:
        return self._post(
            f"{api_version}/{instagram_account_id}/media_publish", params={"creation_id": container_id},
            access_token=access_token, timeout_seconds=timeout_seconds,
        )

    def debug_token(self, *, input_token, access_token, api_version, timeout_seconds) -> dict:
        return self._get(
            f"{api_version}/debug_token", params={"input_token": input_token}, access_token=access_token,
            timeout_seconds=timeout_seconds,
        )

    def get_account_identity(self, *, instagram_account_id, access_token, api_version, timeout_seconds) -> dict:
        return self._get(
            f"{api_version}/{instagram_account_id}", params={"fields": "id,username"}, access_token=access_token,
            timeout_seconds=timeout_seconds,
        )
