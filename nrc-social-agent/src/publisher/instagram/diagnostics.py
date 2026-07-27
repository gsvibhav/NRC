"""Read-only Instagram operational diagnostics (Milestone 11A).

`/instagram-status` (see handlers.py) answers "is Instagram publishing
actually usable right now" without ever creating a media container,
polling one, or calling `publish_media()` — this module uses only the
two read-only operations added to `client.py` for this purpose
(`debug_token()`/`get_account_identity()`). It never touches an
execution or publication record (no `ExecutionManager`/
`PublicationManager` import here), never requires an active workflow,
and never mutates anything.

**Three independent health concepts — never conflated** (see the
Milestone 11A planning report):

- **Application healthy** — the bot process is up and able to answer a
  command at all. Always `True` whenever this diagnostic runs; never
  depends on Meta being reachable, configured, or enabled.
- **Instagram configured** — the two required credentials
  (`INSTAGRAM_ACCOUNT_ID`/`META_ACCESS_TOKEN`) are present, reported
  independently of whether publishing is currently enabled. `Config.
  from_env()` (src/config.py) already fails fast at startup if publishing
  is enabled without them — so in practice this is only ever `False`
  while publishing is also disabled, but the two facts are still
  reported as separate fields rather than collapsed into one.
- **Instagram ready** — enabled AND configured AND the access token is
  currently valid AND it carries the scopes content-publishing requires
  AND the configured Instagram account can actually be looked up. Only
  "ready" ever depends on a live Meta response.

**Diagnostic sequence** (short-circuits at the first failing stage —
never calls a later Meta operation once an earlier one has already
failed): publishing enabled? -> configuration present? -> token
validation (`debug_token`) -> permission validation (same response's
`scopes`) -> Instagram account verification (`get_account_identity`) ->
overall readiness.

**Logging**: only ever logs the stage reached and its boolean outcome —
never the access token, a raw Graph API response, a presigned URL, a
caption, or hashtags.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .client import MetaHttpClient, MetaHttpError

logger = logging.getLogger(__name__)

# The Instagram Login (Business Login) permissions content publishing
# requires, per Meta's own permissions documentation — reported back via
# debug_token()'s `data.scopes` list. Reported only as a pass/fail
# boolean; the token itself is never logged or exposed.
REQUIRED_SCOPES = frozenset({"instagram_business_basic", "instagram_business_content_publish"})


@dataclass(frozen=True)
class InstagramDiagnosticsResult:
    """Everything `/instagram-status` needs to report — never the
    access token, a raw Graph API response, or the full Instagram
    account id."""

    application_healthy: bool
    publishing_enabled: bool
    instagram_configured: bool
    token_valid: bool | None
    permissions_ok: bool | None
    account_verified: bool | None
    account_username: str | None
    instagram_ready: bool
    failure_stage: str | None
    failure_reason: str | None


class InstagramDiagnosticsService:
    """Constructed with the same config values as `InstagramPublisher`
    (src/publisher/instagram/publisher.py) and any `MetaHttpClient`
    implementation — in tests, always a fake/mock; never a real network
    client. Works, and is always safe to call, whether or not Instagram
    publishing is enabled: with publishing disabled it simply reports
    that fact and returns, without importing or requiring any Instagram
    credential to be present."""

    def __init__(
        self,
        *,
        http_client: MetaHttpClient,
        publishing_enabled: bool,
        instagram_account_id: str | None,
        access_token: str | None,
        api_version: str,
        request_timeout_seconds: float,
    ) -> None:
        self._http_client = http_client
        self._publishing_enabled = publishing_enabled
        self._instagram_account_id = instagram_account_id
        self._access_token = access_token
        self._api_version = api_version
        self._request_timeout_seconds = request_timeout_seconds

    def check_status(self) -> InstagramDiagnosticsResult:
        """Never raises: any Meta call failure is captured as a
        diagnostic result field, never propagated as an exception.
        Never creates a container, never publishes, never writes to any
        store."""

        logger.info("Instagram diagnostics started")

        instagram_configured = bool(self._instagram_account_id) and bool(self._access_token)
        logger.info(
            "Instagram diagnostics configuration_status=%s",
            "present" if instagram_configured else "missing",
        )

        if not self._publishing_enabled:
            return self._result(
                publishing_enabled=False, instagram_configured=instagram_configured,
                failure_stage="publishing_disabled", failure_reason="Instagram publishing is not enabled",
            )

        if not instagram_configured:
            return self._result(
                publishing_enabled=True, instagram_configured=False,
                failure_stage="configuration", failure_reason="Instagram credentials are not configured",
            )

        token_valid, scopes, failure_reason = self._check_token()
        logger.info("Instagram diagnostics token_valid=%s", token_valid)
        if not token_valid:
            return self._result(
                publishing_enabled=True, instagram_configured=True, token_valid=False,
                failure_stage="token_validation", failure_reason=failure_reason,
            )

        permissions_ok = REQUIRED_SCOPES.issubset(scopes)
        logger.info("Instagram diagnostics permissions_verified=%s", permissions_ok)
        if not permissions_ok:
            return self._result(
                publishing_enabled=True, instagram_configured=True, token_valid=True, permissions_ok=False,
                failure_stage="permission_validation",
                failure_reason="the access token is missing one or more required permissions",
            )

        account_verified, username, failure_reason = self._check_account()
        logger.info("Instagram diagnostics account_verified=%s", account_verified)
        if not account_verified:
            return self._result(
                publishing_enabled=True, instagram_configured=True, token_valid=True, permissions_ok=True,
                account_verified=False, failure_stage="account_verification", failure_reason=failure_reason,
            )

        logger.info("Instagram diagnostics readiness_result=ready")
        return self._result(
            publishing_enabled=True, instagram_configured=True, token_valid=True, permissions_ok=True,
            account_verified=True, account_username=username, instagram_ready=True,
        )

    @staticmethod
    def _result(
        *, publishing_enabled: bool, instagram_configured: bool,
        token_valid: bool | None = None, permissions_ok: bool | None = None,
        account_verified: bool | None = None, account_username: str | None = None,
        instagram_ready: bool = False, failure_stage: str | None = None, failure_reason: str | None = None,
    ) -> InstagramDiagnosticsResult:
        return InstagramDiagnosticsResult(
            application_healthy=True, publishing_enabled=publishing_enabled,
            instagram_configured=instagram_configured, token_valid=token_valid, permissions_ok=permissions_ok,
            account_verified=account_verified, account_username=account_username, instagram_ready=instagram_ready,
            failure_stage=failure_stage, failure_reason=failure_reason,
        )

    def _check_token(self) -> tuple[bool, frozenset, str | None]:
        try:
            response = self._http_client.debug_token(
                input_token=self._access_token, access_token=self._access_token,
                api_version=self._api_version, timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError:
            logger.warning("Instagram diagnostics token validation call failed")
            return False, frozenset(), "the access token could not be validated"

        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict) or data.get("is_valid") is not True:
            return False, frozenset(), "the access token is invalid or expired"

        scopes = data.get("scopes")
        return True, frozenset(scopes) if isinstance(scopes, list) else frozenset(), None

    def _check_account(self) -> tuple[bool, str | None, str | None]:
        try:
            response = self._http_client.get_account_identity(
                instagram_account_id=self._instagram_account_id, access_token=self._access_token,
                api_version=self._api_version, timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError:
            logger.warning("Instagram diagnostics account verification call failed")
            return False, None, "the configured Instagram account could not be verified"

        username = response.get("username") if isinstance(response, dict) else None
        if not isinstance(username, str) or not username:
            return False, None, "the configured Instagram account could not be verified"
        return True, username, None
