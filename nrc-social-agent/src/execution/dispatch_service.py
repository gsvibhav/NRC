"""`ExecutionDispatchService`: the one orchestrator that turns a
`READY_FOR_DISPATCH` execution into a `COMPLETED` one — claiming
ownership, invoking one or more bounded `Publisher.advance()` steps,
persisting every durable checkpoint, and normalizing the terminal
outcome. Handlers never construct Graph API requests, never poll
container status directly, never update execution status directly, and
never decide whether an error is retryable — all of that lives here and
in the Instagram adapter it calls through the generic `Publisher`
contract.

**The critical acceptance criterion this file exists to guarantee**: the
system must never publish more than once for one execution, even across
Telegram redelivery, a process restart, a Meta timeout after acceptance,
or two concurrent dispatch requests. Three mechanisms combine to make
this true:

1. **OCC-protected claim** (`claim_dispatch()`) — only one caller can
   ever transition `READY_FOR_DISPATCH`/a stale `DISPATCH_IN_PROGRESS`
   lease into an owned `DISPATCH_IN_PROGRESS` state; a losing caller's
   conditional write fails and it reloads and re-evaluates instead of
   proceeding.
2. **Checkpoint-before-call ordering for the one irreversible step**
   (see `_advance_one_step()`'s special-cased `CONTAINER_READY` handling)
   — the `PUBLISH_REQUESTED` checkpoint is durably persisted *before*
   `publish_media()` is ever called, so a crash at any point after that
   write is recoverable without ever risking a second live call: a later
   `advance()` invocation, loading a fresh execution whose checkpoint is
   already `PUBLISH_REQUESTED`, is *statically* routed by
   `InstagramPublisher.advance()` to reconciliation-only logic (see that
   module's own docstring) — the code path that would call
   `publish_media()` again simply cannot be reached once that checkpoint
   is durably persisted.
3. **Conservative ambiguous-outcome handling** — an ambiguous publish
   response (timeout/connection-loss/unparseable body during the
   irreversible call) is never retried. This service reconciles it via
   one read-only container-status check; if that check can't produce
   positive evidence either way, the execution is marked a *permanent*,
   non-retryable failure requiring operator intervention — never a
   silent second attempt. This is a deliberate, documented residual
   limitation: Meta's Content Publishing API provides no reliable
   idempotency key for `media_publish`, so **this system does not claim
   exactly-once delivery** — it claims the strongest achievable
   *at-most-once* guarantee, which is what the critical acceptance
   criterion actually requires.

Container *creation* ambiguity is handled differently (ordinarily
retryable) — see `src/publisher/instagram/publisher.py`'s own docstring
for why creating an extra, harmless, self-expiring container is not the
same risk class as a second live post.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..publication.manager import PublicationManager
from ..publication.models import PublicationPackage, PublicationStatus
from ..publisher.errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from ..publisher.models import FailureCategory, PublisherFailure
from ..publisher.registry import PublisherRegistry
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.states import WorkflowState
from .clock import Clock, Sleeper
from .errors import (
    ExecutionNotEligibleForDispatchError,
    MissingDispatchAuthorizationError,
    PublisherChannelMismatchError,
    PublicationPackageNotFoundForExecutionError,
    UnauthorizedDispatchError,
)
from .manager import ExecutionManager
from .models import DispatchCheckpoint, ExecutionStatus
from .validation import validate_dispatch_dict

logger = logging.getLogger(__name__)

_RETRY_PUBLICATION_DISPATCH = "publication_dispatch"

# A generous but genuinely bounded ceiling on how many advance() steps one
# authorize/retry call may take — covers container creation, N status
# polls, the publish step, and reconciliation — never an unbounded loop.
_MAX_STEPS_PER_CALL = 64


def _now_expired(expires_at: str, now_iso: str) -> bool:
    return expires_at < now_iso


@dataclass(frozen=True)
class DispatchOutcome:
    status: str  # "completed" | "already_completed" | "in_progress" | "retryable_failure" | "permanent_failure"
    execution_id: str
    publication_id: str
    result: dict | None = None
    failure: dict | None = None


class ExecutionDispatchService:
    def __init__(
        self,
        *,
        workflow_manager: WorkflowManager,
        publication_manager: PublicationManager,
        execution_manager: ExecutionManager,
        conversation_manager: ConversationManager,
        publisher_registry: PublisherRegistry,
        clock: Clock,
        sleeper: Sleeper,
        lease_seconds: int,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> None:
        self._workflow_manager = workflow_manager
        self._publication_manager = publication_manager
        self._execution_manager = execution_manager
        self._conversation_manager = conversation_manager
        self._publisher_registry = publisher_registry
        self._clock = clock
        self._sleeper = sleeper
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_poll_checks = max(1, int(poll_timeout_seconds // poll_interval_seconds))

    async def authorize_and_dispatch(
        self, *, publication_id: str, telegram_user_id: int, dispatch_context: dict | None = None
    ) -> DispatchOutcome:
        """Entry point for the explicit "Publish to Instagram" action —
        persists a fresh dispatch authorization if none exists yet, then
        dispatches. Idempotent: a duplicate callback for an
        already-authorized execution reuses the existing authorization.

        `dispatch_context` (Milestone 11B): optional, safe, static
        provenance metadata (e.g. `{"mode": "controlled_live_validation",
        ...}`) merged into the `dispatch_authorization` dict *only* the
        one time this call actually creates it — never overwrites an
        authorization that already exists (mirroring this method's own
        existing idempotency: a second call never re-authorizes). This is
        the *only* difference the controlled live-validation flow
        (src/execution/live_validation.py) introduces into dispatch —
        that flow still calls this exact method, never a second publish
        implementation, and never anything closer to the platform than
        this."""

        return await self._dispatch(
            publication_id=publication_id, telegram_user_id=telegram_user_id, require_existing_authorization=False,
            dispatch_context=dispatch_context,
        )

    async def retry_dispatch(self, *, publication_id: str, telegram_user_id: int) -> DispatchOutcome:
        """Entry point for `/retry` — never creates a fresh dispatch
        authorization; requires one to already exist (raises
        MissingDispatchAuthorizationError otherwise, since `/retry` must
        never be the thing that first authorizes a publish)."""

        return await self._dispatch(
            publication_id=publication_id, telegram_user_id=telegram_user_id, require_existing_authorization=True,
        )

    async def _dispatch(
        self, *, publication_id: str, telegram_user_id: int, require_existing_authorization: bool,
        dispatch_context: dict | None = None,
    ) -> DispatchOutcome:
        loaded = self._execution_manager.load(publication_id)
        if loaded is None:
            raise ExecutionNotEligibleForDispatchError(f"no execution exists for publication_id={publication_id}")
        document, etag = loaded

        try:
            workflow = self._workflow_manager.load_workflow(document.workflow_id)
        except WorkflowError as exc:
            raise ExecutionNotEligibleForDispatchError(f"failed to load workflow_id={document.workflow_id}: {exc}") from exc

        if workflow.telegram_user_id != telegram_user_id:
            logger.warning(
                "Unauthorized dispatch attempt publication_id=%s telegram_user_id=%s", publication_id, telegram_user_id
            )
            raise UnauthorizedDispatchError(f"telegram_user_id={telegram_user_id} does not own workflow_id={document.workflow_id}")

        # --- idempotency: zero Meta calls for an already-resolved execution ---
        if document.status is ExecutionStatus.COMPLETED:
            return DispatchOutcome(
                status="already_completed", execution_id=document.execution_id, publication_id=publication_id,
                result=document.result,
            )

        if document.status is ExecutionStatus.DISPATCH_IN_PROGRESS:
            lease = document.lease or {}
            if not _now_expired(lease.get("expires_at", ""), self._clock.now_iso()):
                return DispatchOutcome(
                    status="in_progress", execution_id=document.execution_id, publication_id=publication_id,
                )
            # Expired lease: recover the existing platform state, never
            # start over from scratch and never treat this as a fresh
            # externally-meaningful attempt.
            increment_attempt = False
        elif document.status is ExecutionStatus.DISPATCH_FAILED:
            failure = document.failure or {}
            if not failure.get("retryable", False):
                return DispatchOutcome(
                    status="permanent_failure", execution_id=document.execution_id, publication_id=publication_id,
                    failure=document.failure,
                )
            # An ambiguous-outcome reconciliation attempt is a read-only
            # recovery of the *same* prior attempt, never a fresh
            # externally-meaningful one — see
            # src/publisher/instagram/publisher.py's `_reconcile_ambiguous_publish()`.
            increment_attempt = failure.get("category") != FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME.value
        elif document.status is ExecutionStatus.READY_FOR_DISPATCH:
            increment_attempt = True
        else:
            raise ExecutionNotEligibleForDispatchError(f"unexpected execution status for dispatch: {document.status!r}")

        # --- authorization: persisted before any Meta call -------------------
        if document.dispatch_authorization is None:
            if require_existing_authorization:
                raise MissingDispatchAuthorizationError(
                    f"no dispatch authorization exists yet for publication_id={publication_id}"
                )
            authorization = {
                "authorized_at": self._clock.now_iso(),
                "authorized_by_telegram_user_id": telegram_user_id,
                "publication_id": publication_id,
                "execution_id": document.execution_id,
            }
            if dispatch_context:
                validate_dispatch_dict("dispatch_authorization.dispatch_context", dispatch_context)
                authorization["dispatch_context"] = dispatch_context
            validate_dispatch_dict("dispatch_authorization", authorization)
            document, etag = self._execution_manager.authorize_dispatch(
                publication_id=publication_id, expected_etag=etag, authorization=authorization,
            )

        # --- resolve and re-verify the authoritative publication package ----
        output_id = (workflow.publication or {}).get("output_id")
        package = self._publication_manager.find_existing(document.workflow_id, output_id) if output_id else None
        if package is None or package.status is not PublicationStatus.READY_FOR_PUBLISHING:
            raise PublicationPackageNotFoundForExecutionError(
                f"no READY_FOR_PUBLISHING package found for workflow_id={document.workflow_id} output_id={output_id}"
            )
        if package.publication_id != document.publication_id or package.channel != document.channel:
            raise PublisherChannelMismatchError(
                f"execution/publication linkage mismatch for publication_id={publication_id}"
            )

        publisher = self._publisher_registry.resolve(document.publisher)

        # --- claim ownership --------------------------------------------------
        lease = {
            "owner_id": uuid.uuid4().hex,
            "acquired_at": self._clock.now_iso(),
            "expires_at": self._lease_expiry(),
        }
        document, etag = self._execution_manager.claim_dispatch(
            publication_id=publication_id, expected_etag=etag, lease=lease, increment_attempt=increment_attempt,
        )

        return await self._run_steps(document=document, etag=etag, publication=package, publisher=publisher)

    def _lease_expiry(self) -> str:
        from datetime import datetime, timedelta, timezone

        return (datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds)).isoformat()

    async def _run_steps(self, *, document, etag: str, publication: PublicationPackage, publisher) -> DispatchOutcome:
        publication_id = document.publication_id
        poll_checks_used = 0

        for _ in range(_MAX_STEPS_PER_CALL):
            try:
                if document.checkpoint is DispatchCheckpoint.CONTAINER_READY:
                    # Durable "about to attempt the irreversible call"
                    # marker, persisted BEFORE the call. `pre_transition`
                    # (still showing CONTAINER_READY) is what we hand to
                    # advance() so it performs the actual publish attempt
                    # exactly once; the durably-persisted checkpoint is
                    # already PUBLISH_REQUESTED by the time that call is
                    # made — see this module's own docstring.
                    pre_transition = document
                    document, etag = self._execution_manager.persist_checkpoint(
                        publication_id=publication_id, expected_etag=etag,
                        checkpoint=DispatchCheckpoint.PUBLISH_REQUESTED,
                        platform_state_update={"publish_request_started_at": self._clock.now_iso()},
                    )
                    step_result = publisher.advance(pre_transition, publication)
                else:
                    step_result = publisher.advance(document, publication)
            except PublisherAmbiguousError as exc:
                # Marked retryable=True *not* to permit blindly repeating
                # the irreversible call again, but specifically to allow
                # exactly one thing on a later call: reconciliation.
                # `checkpoint` is already durably PUBLISH_REQUESTED (see
                # above), and dispatch_service._dispatch() never
                # increments `attempt` for this category — see there.
                # InstagramPublisher.advance() statically routes any
                # future call at this checkpoint to reconciliation-only
                # logic, which is itself what may eventually mark this
                # execution genuinely, non-retryably failed.
                failure = exc.failure or PublisherFailure(
                    category=FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME, retryable=True, operation="publish_media",
                    safe_message="Instagram publication outcome could not be confirmed yet.",
                    occurred_at=self._clock.now_iso(),
                )
                validate_dispatch_dict("failure", failure.to_dict())
                document = self._execution_manager.fail_dispatch(
                    publication_id=publication_id, expected_etag=etag, failure=failure.to_dict(),
                )
                self._sync_pending_retry(document.workflow_id, document.failure)
                return DispatchOutcome(
                    status="retryable_failure", execution_id=document.execution_id, publication_id=publication_id,
                    failure=document.failure,
                )
            except PublisherPermanentError as exc:
                failure = exc.failure or self._generic_failure("dispatch", retryable=False)
                validate_dispatch_dict("failure", failure.to_dict())
                document = self._execution_manager.fail_dispatch(
                    publication_id=publication_id, expected_etag=etag, failure=failure.to_dict(),
                    checkpoint=exc.reset_checkpoint,
                )
                self._sync_pending_retry(document.workflow_id, document.failure)
                return DispatchOutcome(
                    status="permanent_failure", execution_id=document.execution_id, publication_id=publication_id,
                    failure=document.failure,
                )
            except PublisherRetryableError as exc:
                failure = exc.failure or self._generic_failure("dispatch", retryable=True)
                validate_dispatch_dict("failure", failure.to_dict())
                document = self._execution_manager.fail_dispatch(
                    publication_id=publication_id, expected_etag=etag, failure=failure.to_dict(),
                )
                self._sync_pending_retry(document.workflow_id, document.failure)
                return DispatchOutcome(
                    status="retryable_failure", execution_id=document.execution_id, publication_id=publication_id,
                    failure=document.failure,
                )

            if step_result.result is not None:
                validate_dispatch_dict("result", step_result.result.to_dict())
                document = self._execution_manager.complete_dispatch(
                    publication_id=publication_id, expected_etag=etag, checkpoint=step_result.next_checkpoint,
                    result=step_result.result.to_dict(),
                )
                self._sync_pending_retry(document.workflow_id, None)
                self._finish_success(document.workflow_id)
                return DispatchOutcome(
                    status="completed", execution_id=document.execution_id, publication_id=publication_id,
                    result=document.result,
                )

            if step_result.next_checkpoint is not DispatchCheckpoint.PUBLISH_REQUESTED:
                # (the PUBLISH_REQUESTED transition was already persisted
                # above, before the irreversible call — never persisted
                # twice for the same step.)
                validate_dispatch_dict("platform_state", step_result.platform_state_update)
                document, etag = self._execution_manager.persist_checkpoint(
                    publication_id=publication_id, expected_etag=etag, checkpoint=step_result.next_checkpoint,
                    platform_state_update=step_result.platform_state_update,
                )

            if step_result.next_checkpoint is DispatchCheckpoint.CONTAINER_PROCESSING:
                poll_checks_used += 1
                if poll_checks_used >= self._max_poll_checks:
                    failure = PublisherFailure(
                        category=FailureCategory.CONTAINER_PROCESSING_TIMEOUT, retryable=True, operation="get_container_status",
                        safe_message="Instagram is still processing the media; this can be retried shortly.",
                        occurred_at=self._clock.now_iso(),
                    )
                    document = self._execution_manager.fail_dispatch(
                        publication_id=publication_id, expected_etag=etag, failure=failure.to_dict(),
                    )
                    self._sync_pending_retry(document.workflow_id, document.failure)
                    return DispatchOutcome(
                        status="retryable_failure", execution_id=document.execution_id, publication_id=publication_id,
                        failure=document.failure,
                    )
                await self._sleeper.sleep(self._poll_interval_seconds)

        # Exhausted the per-call step budget without reaching a terminal
        # outcome — treated as retryable; checkpoint/platform_state are
        # already durably persisted, so a subsequent call resumes exactly
        # here rather than restarting.
        failure = self._generic_failure("dispatch", retryable=True)
        document = self._execution_manager.fail_dispatch(publication_id=publication_id, expected_etag=etag, failure=failure.to_dict())
        self._sync_pending_retry(document.workflow_id, document.failure)
        return DispatchOutcome(
            status="retryable_failure", execution_id=document.execution_id, publication_id=publication_id,
            failure=document.failure,
        )

    def _sync_pending_retry(self, workflow_id: str, failure: dict | None) -> None:
        """Mirrors the `pending_retry` bookkeeping every earlier pipeline
        stage uses (see src/publication/service.py, src/execution/service.py)
        so `/retry`'s existing dispatch-by-metadata mechanism works for
        `"publication_dispatch"` unchanged. `failure=None` (success) or a
        non-retryable failure clears it; a retryable failure sets it."""

        pending_retry = _RETRY_PUBLICATION_DISPATCH if (failure or {}).get("retryable") else None
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": pending_retry})
        except WorkflowError:
            logger.error("Failed to sync pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _generic_failure(self, operation: str, *, retryable: bool) -> PublisherFailure:
        return PublisherFailure(
            category=FailureCategory.UNKNOWN_PLATFORM_ERROR, retryable=retryable, operation=operation,
            safe_message="Instagram could not complete this request right now.", occurred_at=self._clock.now_iso(),
        )

    def _finish_success(self, workflow_id: str) -> None:
        try:
            workflow = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError:
            logger.error("Failed to load workflow for pointer-clear workflow_id=%s", workflow_id, exc_info=True)
            return
        clear_pointer_if_terminal(
            telegram_user_id=workflow.telegram_user_id, workflow_state=WorkflowState.COMPLETED,
            conversation_manager=self._conversation_manager,
        )
