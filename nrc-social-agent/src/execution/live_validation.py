"""`ControlledLiveValidationService` (Milestone 11B): the operator-only,
explicitly-confirmed gate in front of the very first real Instagram
publication this system will ever perform.

**This module never publishes anything itself.** It locates one eligible
execution, renders a safe confirmation preview, persists a durable
AWAITING_CONFIRMATION record, re-validates everything a second time at
confirmation, and — only then — calls the exact same
`ExecutionDispatchService.authorize_and_dispatch()` every ordinary
"Publish to Instagram" click already calls (see dispatch_service.py).
There is no second publish implementation here, no direct
`MetaHttpClient`/`InstagramPublisher` call, and no code path that reaches
`publish_media()` other than the one Milestone 10 already built and this
module's own docstring below documents.

**Durable state, not a transient button alone.** A confirmation preview
message is transient (Telegram can drop or duplicate it), but the
*authorization* it leads to is not: `build_preview()` persists an
AWAITING_CONFIRMATION record directly on the target execution document,
in `metadata["live_validation"]` — extending the *existing* execution
domain and its established OCC-protected write pattern
(`ExecutionManager.set_live_validation_state()`), rather than inventing a
wholly separate S3 namespace/store. This was a deliberate choice: the
confirmation is fundamentally about "is it still safe to dispatch *this*
execution," which is exactly the execution domain's own concern, and
reusing its OCC/etag machinery gives one-time-use consumption "for free"
(see `confirm()`'s docstring below) rather than requiring a second
concurrency mechanism.

**Eligibility is resolved through the existing ownership-safe active-
workflow pointer** (`resolve_active_workflow()`, the same mechanism
`/status`/`/cancel`/`/retry`/`dispatch_action()` all already use) —
never through a new S3 listing capability. Because only one workflow can
ever be active per Telegram user at a time (Milestone 3.5's own
upload-refusal rule), there is structurally at most one eligible
candidate reachable this way today; the "multiple eligible" selection
path is still implemented (see `find_eligible_candidates()`'s return
shape and handlers.py's selection-list rendering) for forward
compatibility and is exercised by a synthetic unit test, but is not
reachable through the real service under the current one-active-
workflow-per-operator architecture — see README.md's "Known
limitations."

**Scope is deliberately narrow for the first live validation**: only a
package whose resolved placement is Feed + single JPEG image is
eligible (`_check_eligibility()`) — Reels and any other media are
rejected here regardless of what the Instagram adapter itself supports
internally.

**Re-validation happens twice, never once**: `build_preview()` checks
eligibility, freshness, and Instagram readiness once, at preview time;
`confirm()` independently re-checks every one of those same facts again
— operator identity, confirmation token, expiry, one-time consumption,
execution status/checkpoint/attempt, publication package digest,
Instagram account fingerprint, and Instagram readiness — immediately
before ever calling `authorize_and_dispatch()`. Any mismatch raises and
dispatches nothing; the operator must request a fresh preview."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..conversation.errors import ConversationError, NoActiveWorkflowError
from ..conversation.manager import ConversationManager
from ..conversation.resolution import resolve_active_workflow
from ..publication.manager import PublicationManager
from ..publication.models import MediaMode, Placement, PublicationPackage, PublicationStatus
from ..publisher.models import FailureCategory
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from .clock import Clock
from .dispatch_service import DispatchOutcome, ExecutionDispatchService
from .errors import (
    ExecutionConcurrentModificationError,
    LiveValidationConfirmationExpiredError,
    LiveValidationConfirmationNotFoundError,
    LiveValidationNotReadyError,
    LiveValidationStateChangedError,
    NoEligibleLiveValidationCandidateError,
    NotLiveTestOperatorError,
)
from .manager import ExecutionManager
from .models import ExecutionStatus
from .validation import validate_dispatch_dict

logger = logging.getLogger(__name__)

_CONFIRMATION_TTL_SECONDS = 300  # 5 minutes — deliberately short-lived.
_LIVE_VALIDATION_MODE = "controlled_live_validation"


@dataclass(frozen=True)
class LiveValidationCandidate:
    workflow_id: str
    publication_id: str
    execution_id: str
    output_id: str


@dataclass(frozen=True)
class LiveValidationPreview:
    publication_id: str
    execution_id: str
    confirmation_token: str
    expires_at: str
    account_username: str
    account_fingerprint: str
    placement: str
    media_mode: str
    approved_version_number: int | None
    media_count: int


def _compute_package_digest(package: PublicationPackage) -> str:
    """A deterministic, safe short fingerprint of the *entire* persisted
    package — never partial, so any change at all (content, media,
    placement, approval) is detected. Never itself contains a secret;
    package content has no token/URL fields to begin with (see
    publication/models.py)."""

    payload = json.dumps(package.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _lease_active(lease: dict | None, now_iso: str) -> bool:
    if not lease:
        return False
    return lease.get("expires_at", "") >= now_iso


class ControlledLiveValidationService:
    def __init__(
        self,
        *,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
        publication_manager: PublicationManager,
        execution_manager: ExecutionManager,
        execution_dispatch_service: ExecutionDispatchService,
        diagnostics_service,
        clock: Clock,
        instagram_account_id: str | None,
        live_test_operator_ids: frozenset[int],
    ) -> None:
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager
        self._publication_manager = publication_manager
        self._execution_manager = execution_manager
        self._execution_dispatch_service = execution_dispatch_service
        self._diagnostics_service = diagnostics_service
        self._clock = clock
        self._instagram_account_id = instagram_account_id
        self._live_test_operator_ids = live_test_operator_ids

    # --- Part D/E: eligibility + selection -----------------------------------

    def find_eligible_candidates(self, *, telegram_user_id: int) -> tuple[list[LiveValidationCandidate], str | None]:
        """Returns `(candidates, reason_if_empty)`. Never raises for an
        ordinary "nothing eligible" outcome — only for a genuine
        authorization failure (not a live-test operator)."""

        self._require_operator(telegram_user_id)

        try:
            workflow = resolve_active_workflow(telegram_user_id, self._conversation_manager, self._workflow_manager)
        except (NoActiveWorkflowError, ConversationError):
            return [], "No active, eligible publication was found for controlled live validation."

        publication_ref = workflow.publication or {}
        output_id = publication_ref.get("output_id")
        publication_id = publication_ref.get("publication_id")
        if not output_id or not publication_id:
            return [], "Your active post has no prepared publication package yet."

        package = self._publication_manager.find_existing(workflow.workflow_id, output_id)
        loaded = self._execution_manager.load(publication_id)
        execution = loaded[0] if loaded is not None else None

        diagnostics_result = self._diagnostics_service.check_status()
        if not diagnostics_result.instagram_ready:
            reason = diagnostics_result.failure_reason or "Instagram is not currently ready."
            return [], f"Instagram is not currently ready for a live publication ({reason})."

        ok, reason = self._check_eligibility(execution=execution, package=package)
        if not ok:
            return [], reason

        candidate = LiveValidationCandidate(
            workflow_id=workflow.workflow_id, publication_id=publication_id,
            execution_id=execution.execution_id, output_id=output_id,
        )
        return [candidate], None

    def _check_eligibility(self, *, execution, package) -> tuple[bool, str | None]:
        if package is None:
            return False, "No prepared publication package was found."
        if package.status is not PublicationStatus.READY_FOR_PUBLISHING:
            return False, "The publication package is not ready for publishing."
        if execution is None:
            return False, "No execution record was found for that publication."
        if execution.status is ExecutionStatus.COMPLETED:
            return False, "That publication has already been published."
        if execution.status is ExecutionStatus.DISPATCH_IN_PROGRESS:
            if _lease_active(execution.lease, self._clock.now_iso()):
                return False, "A dispatch is already in progress for that publication."
        if execution.status is ExecutionStatus.DISPATCH_FAILED:
            failure = execution.failure or {}
            if failure.get("category") == FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME.value:
                return False, (
                    "A prior publish attempt has an unresolved, ambiguous outcome that requires manual "
                    "review — see docs/INSTAGRAM_LIVE_VALIDATION.md."
                )
            if not failure.get("retryable", False):
                return False, "That publication permanently failed to dispatch and is not eligible here."

        media_type = package.media[0].get("media_type") if package.media else None
        if media_type != "image/jpeg":
            return False, "Only a single JPEG Feed image is supported for controlled live validation right now."
        try:
            resolved = package.resolved_placement()
        except Exception:
            return False, "That publication's placement could not be determined."
        if resolved.placement is not Placement.FEED or resolved.media_mode is not MediaMode.SINGLE_IMAGE:
            return False, "Only a single JPEG Feed image is supported for controlled live validation right now."

        return True, None

    # --- Part F/H: confirmation preview + durable AWAITING_CONFIRMATION -----

    def build_preview(self, *, telegram_user_id: int, publication_id: str) -> LiveValidationPreview:
        self._require_operator(telegram_user_id)

        loaded = self._execution_manager.load(publication_id)
        if loaded is None:
            raise NoEligibleLiveValidationCandidateError(f"no execution exists for publication_id={publication_id}")
        execution, etag = loaded

        try:
            workflow = self._workflow_manager.load_workflow(execution.workflow_id)
        except WorkflowError as exc:
            raise NoEligibleLiveValidationCandidateError(str(exc)) from exc
        if workflow.telegram_user_id != telegram_user_id:
            logger.warning(
                "Unauthorized live-validation preview attempt user_id=%s publication_id=%s",
                telegram_user_id, publication_id,
            )
            raise NotLiveTestOperatorError(f"telegram_user_id={telegram_user_id} does not own this publication")

        output_id = (workflow.publication or {}).get("output_id")
        package = self._publication_manager.find_existing(execution.workflow_id, output_id) if output_id else None

        diagnostics_result = self._diagnostics_service.check_status()
        if not diagnostics_result.instagram_ready:
            raise LiveValidationNotReadyError(diagnostics_result.failure_reason or "Instagram is not ready")

        ok, reason = self._check_eligibility(execution=execution, package=package)
        if not ok:
            raise NoEligibleLiveValidationCandidateError(reason or "not eligible")

        account_fingerprint = self._account_fingerprint()
        package_digest = _compute_package_digest(package)
        # 12 hex chars (48 bits) — deliberately short so the Telegram
        # callback_data `ilv:c:<publication_id>:<token>` stays
        # comfortably under Telegram's 64-byte callback_data ceiling
        # (see handlers.py's _build_live_validation_confirmation_keyboard()).
        # Entropy is sufficient here because the token is additionally
        # single-operator-scoped, short-lived, and one-time-use.
        token = secrets.token_hex(6)
        now = self._clock.now_iso()
        expires_at = _expiry_from(now)

        live_validation = {
            "mode": _LIVE_VALIDATION_MODE,
            "status": "AWAITING_CONFIRMATION",
            "confirmation_token": token,
            "operator_telegram_user_id": telegram_user_id,
            "created_at": now,
            "expires_at": expires_at,
            "expected_package_digest": package_digest,
            "expected_account_fingerprint": account_fingerprint,
            "expected_execution_status": execution.status.value,
            "expected_execution_checkpoint": execution.checkpoint.value,
            "expected_execution_attempt": execution.attempt,
        }
        validate_dispatch_dict("metadata.live_validation", live_validation)

        try:
            self._execution_manager.set_live_validation_state(
                publication_id=publication_id, expected_etag=etag, live_validation=live_validation,
            )
        except ExecutionConcurrentModificationError as exc:
            raise LiveValidationStateChangedError(
                f"execution changed while building the preview for publication_id={publication_id}"
            ) from exc

        placement = package.resolved_placement()
        return LiveValidationPreview(
            publication_id=publication_id, execution_id=execution.execution_id, confirmation_token=token,
            expires_at=expires_at, account_username=diagnostics_result.account_username or "unknown",
            account_fingerprint=account_fingerprint, placement=placement.placement.value,
            media_mode=placement.media_mode.value,
            approved_version_number=(package.draft or {}).get("version_number"), media_count=len(package.media),
        )

    # --- Part G/H/I: confirmation, one-time consumption, dispatch reuse -----

    async def confirm(self, *, telegram_user_id: int, publication_id: str, confirmation_token: str) -> DispatchOutcome:
        """Re-validates everything a fresh preview already validated, then
        atomically consumes the confirmation (via the same OCC `expected_etag`
        write pattern used everywhere else in this domain) before ever
        calling `ExecutionDispatchService.authorize_and_dispatch()`. Two
        concurrent calls can never both reach that call: whichever loses
        the OCC-protected consumption write raises
        `LiveValidationConfirmationNotFoundError` and dispatches nothing."""

        self._require_operator(telegram_user_id)

        loaded = self._execution_manager.load(publication_id)
        if loaded is None:
            raise LiveValidationConfirmationNotFoundError(f"no execution exists for publication_id={publication_id}")
        execution, etag = loaded

        try:
            workflow = self._workflow_manager.load_workflow(execution.workflow_id)
        except WorkflowError as exc:
            raise LiveValidationConfirmationNotFoundError(str(exc)) from exc
        if workflow.telegram_user_id != telegram_user_id:
            logger.warning(
                "Unauthorized live-validation confirm attempt user_id=%s publication_id=%s",
                telegram_user_id, publication_id,
            )
            raise NotLiveTestOperatorError(f"telegram_user_id={telegram_user_id} does not own this publication")

        live_validation = self._require_awaiting_confirmation(execution, confirmation_token, telegram_user_id)

        now = self._clock.now_iso()
        if live_validation.get("expires_at", "") < now:
            raise LiveValidationConfirmationExpiredError(f"confirmation for publication_id={publication_id} expired")

        if (
            execution.status.value != live_validation.get("expected_execution_status")
            or execution.checkpoint.value != live_validation.get("expected_execution_checkpoint")
            or execution.attempt != live_validation.get("expected_execution_attempt")
        ):
            raise LiveValidationStateChangedError(f"execution state changed for publication_id={publication_id}")

        output_id = (workflow.publication or {}).get("output_id")
        package = self._publication_manager.find_existing(execution.workflow_id, output_id) if output_id else None
        if package is None or _compute_package_digest(package) != live_validation.get("expected_package_digest"):
            raise LiveValidationStateChangedError(f"publication package changed for publication_id={publication_id}")

        diagnostics_result = self._diagnostics_service.check_status()
        if not diagnostics_result.instagram_ready:
            raise LiveValidationNotReadyError(diagnostics_result.failure_reason or "Instagram is not ready")
        if self._account_fingerprint() != live_validation.get("expected_account_fingerprint"):
            raise LiveValidationStateChangedError(f"Instagram account changed for publication_id={publication_id}")

        ok, reason = self._check_eligibility(execution=execution, package=package)
        if not ok:
            raise NoEligibleLiveValidationCandidateError(reason or "not eligible")

        consumed = {**live_validation, "status": "CONFIRMED", "confirmed_at": now}
        validate_dispatch_dict("metadata.live_validation", consumed)
        try:
            self._execution_manager.set_live_validation_state(
                publication_id=publication_id, expected_etag=etag, live_validation=consumed,
            )
        except ExecutionConcurrentModificationError as exc:
            raise LiveValidationConfirmationNotFoundError(
                f"confirmation for publication_id={publication_id} was already consumed"
            ) from exc

        dispatch_context = {
            "mode": _LIVE_VALIDATION_MODE,
            "operator_telegram_user_id": telegram_user_id,
            "confirmed_at": now,
            "account_fingerprint": self._account_fingerprint(),
        }
        logger.info(
            "Controlled live validation confirmed publication_id=%s operator_id=%s",
            publication_id, telegram_user_id,
        )
        return await self._execution_dispatch_service.authorize_and_dispatch(
            publication_id=publication_id, telegram_user_id=telegram_user_id, dispatch_context=dispatch_context,
        )

    # --- Part G: cancel -------------------------------------------------------

    def cancel(self, *, telegram_user_id: int, publication_id: str, confirmation_token: str) -> None:
        """Best-effort and idempotent — never raises for an already-
        resolved (confirmed/cancelled/expired/missing) confirmation, since
        from the operator's perspective "nothing will be published" is
        already true in every one of those cases."""

        self._require_operator(telegram_user_id)

        loaded = self._execution_manager.load(publication_id)
        if loaded is None:
            return
        execution, etag = loaded

        live_validation = (execution.metadata or {}).get("live_validation")
        if not isinstance(live_validation, dict) or live_validation.get("status") != "AWAITING_CONFIRMATION":
            return
        if live_validation.get("confirmation_token") != confirmation_token:
            return
        if live_validation.get("operator_telegram_user_id") != telegram_user_id:
            return

        cancelled = {**live_validation, "status": "CANCELLED", "cancelled_at": self._clock.now_iso()}
        try:
            self._execution_manager.set_live_validation_state(
                publication_id=publication_id, expected_etag=etag, live_validation=cancelled,
            )
        except ExecutionConcurrentModificationError:
            pass
        logger.info("Controlled live validation cancelled publication_id=%s operator_id=%s", publication_id, telegram_user_id)

    # --- shared helpers ---------------------------------------------------

    def _require_operator(self, telegram_user_id: int) -> None:
        if telegram_user_id not in self._live_test_operator_ids:
            logger.warning("Non-operator attempted controlled live validation user_id=%s", telegram_user_id)
            raise NotLiveTestOperatorError(f"telegram_user_id={telegram_user_id} is not an authorized live-test operator")

    def _account_fingerprint(self) -> str:
        account_id = self._instagram_account_id or ""
        suffix = account_id[-4:] if len(account_id) >= 4 else account_id
        return f"••••{suffix}" if suffix else "••••"

    @staticmethod
    def _require_awaiting_confirmation(execution, confirmation_token: str, telegram_user_id: int) -> dict:
        live_validation = (execution.metadata or {}).get("live_validation")
        if not isinstance(live_validation, dict) or live_validation.get("status") != "AWAITING_CONFIRMATION":
            raise LiveValidationConfirmationNotFoundError(
                f"no AWAITING_CONFIRMATION state for publication_id={execution.publication_id}"
            )
        if live_validation.get("confirmation_token") != confirmation_token:
            raise LiveValidationConfirmationNotFoundError("confirmation token does not match")
        if live_validation.get("operator_telegram_user_id") != telegram_user_id:
            raise NotLiveTestOperatorError(
                f"telegram_user_id={telegram_user_id} did not create this confirmation"
            )
        return live_validation


def _expiry_from(now_iso: str) -> str:
    return (datetime.fromisoformat(now_iso) + timedelta(seconds=_CONFIRMATION_TTL_SECONDS)).isoformat()
