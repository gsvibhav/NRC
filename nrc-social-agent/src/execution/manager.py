"""Business logic for execution-record persistence: create, look up, and
(Milestone 10) the mechanical dispatch-lifecycle writes.

Mirrors src/publication/manager.py. Every dispatch-lifecycle method here
is a single OCC-protected write — no business logic (claim eligibility,
checkpoint sequencing, recovery decisions) lives here; that's
`dispatch_service.py`'s job. This mirrors the established
"manager = mechanical persistence + OCC, service = orchestration"
split used throughout this codebase.

`create_execution()` uses a conditional create (`IfNoneMatch="*"`) — an
execution record is never silently overwritten by a second concurrent
creation attempt. A losing create surfaces
`ExecutionConcurrentModificationError` to the caller (service.py), which
reconciles it by re-reading the winner's already-persisted record rather
than treating it as a user-facing failure — see that module's docstring.
Every dispatch-lifecycle write below surfaces the same exception on a
losing conditional update; `dispatch_service.py` reconciles those by
reloading and re-evaluating the authoritative execution, never assuming
its own in-memory copy is still current.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .errors import ExecutionNotFoundError
from .models import DispatchCheckpoint, ExecutionDocument, ExecutionStatus
from .repository import ExecutionRepository

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionManager:
    def __init__(self, repository: ExecutionRepository) -> None:
        self._repository = repository

    def find_existing(self, publication_id: str) -> ExecutionDocument | None:
        """Return the existing execution record for this publication_id,
        or None if none has ever been created. This is the idempotency
        check callers must run before creating a new one."""

        try:
            loaded = self._repository.load(publication_id)
        except ExecutionNotFoundError:
            return None
        return loaded.document

    def load(self, publication_id: str) -> tuple[ExecutionDocument, str] | None:
        """Like find_existing(), but also returns the ETag — every
        dispatch-lifecycle operation needs it for OCC. Returns None if no
        execution exists yet (should be unreachable for dispatch, which
        only ever runs against an execution Milestone 9 already created)."""

        try:
            loaded = self._repository.load(publication_id)
        except ExecutionNotFoundError:
            return None
        return loaded.document, loaded.etag

    def create_execution(
        self,
        *,
        publication_id: str,
        workflow_id: str,
        channel,
        publisher: str,
        metadata: dict | None = None,
    ) -> ExecutionDocument:
        """Create and persist a new execution record, always in its
        initial `READY_FOR_DISPATCH` status with `attempt=0` — this
        milestone never constructs any other status. Raises
        ExecutionConcurrentModificationError if one already exists for
        this publication_id."""

        now = _now_iso()
        document = ExecutionDocument(
            execution_id=f"exec_{uuid.uuid4().hex}",
            publication_id=publication_id,
            workflow_id=workflow_id,
            channel=channel,
            status=ExecutionStatus.READY_FOR_DISPATCH,
            attempt=0,
            publisher=publisher,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._repository.save(document, expected_etag=None)
        logger.info(
            "Execution record created publication_id=%s workflow_id=%s execution_id=%s publisher=%s",
            publication_id, workflow_id, document.execution_id, publisher,
        )
        return document

    # --- Milestone 10: dispatch lifecycle -----------------------------------

    def authorize_dispatch(
        self, *, publication_id: str, expected_etag: str, authorization: dict
    ) -> tuple[ExecutionDocument, str]:
        """Persist explicit Publish authorization — always the first
        dispatch-lifecycle write for a given execution, strictly before
        any Meta call. Does not itself change `status`/`checkpoint`."""

        loaded = self._repository.load(publication_id)
        updated = replace(loaded.document, dispatch_authorization=authorization, updated_at=_now_iso())
        etag = self._repository.save(updated, expected_etag=expected_etag)
        logger.info("Dispatch authorization persisted publication_id=%s", publication_id)
        return updated, etag

    def claim_dispatch(
        self, *, publication_id: str, expected_etag: str, lease: dict, increment_attempt: bool
    ) -> tuple[ExecutionDocument, str]:
        """OCC transition to `DISPATCH_IN_PROGRESS` with a fresh lease.
        Never resets `checkpoint`/`platform_state` — recovery resumes
        from whatever was last durably persisted, never from scratch.
        `increment_attempt` is decided by the caller (dispatch_service.py)
        per this milestone's own "increment once per externally
        meaningful dispatch attempt" rule."""

        loaded = self._repository.load(publication_id)
        document = loaded.document
        updated = replace(
            document,
            status=ExecutionStatus.DISPATCH_IN_PROGRESS,
            attempt=document.attempt + (1 if increment_attempt else 0),
            lease=lease,
            failure=None,
            updated_at=_now_iso(),
        )
        etag = self._repository.save(updated, expected_etag=expected_etag)
        logger.info(
            "Execution claimed for dispatch publication_id=%s attempt=%s owner_id=%s",
            publication_id, updated.attempt, lease.get("owner_id"),
        )
        return updated, etag

    def persist_checkpoint(
        self, *, publication_id: str, expected_etag: str, checkpoint: DispatchCheckpoint, platform_state_update: dict | None
    ) -> tuple[ExecutionDocument, str]:
        """Advance the durable checkpoint, merging `platform_state_update`
        into any existing `platform_state` — called after every
        irreversible/durable platform step, always *before* the dispatch
        service proceeds to the next step (see dispatch_service.py)."""

        loaded = self._repository.load(publication_id)
        document = loaded.document
        merged_platform_state = {**(document.platform_state or {}), **(platform_state_update or {})}
        updated = replace(
            document, checkpoint=checkpoint, platform_state=merged_platform_state, updated_at=_now_iso()
        )
        etag = self._repository.save(updated, expected_etag=expected_etag)
        logger.info(
            "Dispatch checkpoint persisted publication_id=%s checkpoint=%s", publication_id, checkpoint.value
        )
        return updated, etag

    def complete_dispatch(
        self, *, publication_id: str, expected_etag: str, checkpoint: DispatchCheckpoint, result: dict
    ) -> ExecutionDocument:
        """Terminal success write: status -> COMPLETED, normalized result
        persisted, lease and any prior failure cleared. One OCC-protected
        write (S3 can't atomically update multiple objects, so completion
        is kept entirely within this single execution document)."""

        loaded = self._repository.load(publication_id)
        document = loaded.document
        updated = replace(
            document,
            status=ExecutionStatus.COMPLETED,
            checkpoint=checkpoint,
            result=result,
            failure=None,
            lease=None,
            updated_at=_now_iso(),
        )
        self._repository.save(updated, expected_etag=expected_etag)
        logger.info("Execution dispatch completed publication_id=%s", publication_id)
        return updated

    def fail_dispatch(
        self, *, publication_id: str, expected_etag: str, failure: dict, checkpoint: DispatchCheckpoint | None = None
    ) -> ExecutionDocument:
        """Terminal-for-now failure write: status -> DISPATCH_FAILED,
        normalized failure persisted, lease released. `checkpoint` is
        deliberately **preserved by default, never reset** — recovery/
        retry resumes from the last durable platform step, per this
        milestone's own recovery requirement, rather than restarting the
        whole flow. The one narrow exception: a caller may pass an
        explicit `checkpoint` override (e.g. back to `NOT_STARTED`) for
        the specific case where the in-flight platform state itself is
        now dead and unusable (an expired/errored Instagram container) —
        see src/publisher/instagram/publisher.py's `reset_checkpoint`."""

        loaded = self._repository.load(publication_id)
        document = loaded.document
        updated = replace(
            document, status=ExecutionStatus.DISPATCH_FAILED, checkpoint=checkpoint or document.checkpoint,
            failure=failure, lease=None, updated_at=_now_iso(),
        )
        self._repository.save(updated, expected_etag=expected_etag)
        logger.info(
            "Execution dispatch failed publication_id=%s retryable=%s",
            publication_id, (failure or {}).get("retryable"),
        )
        return updated

    # --- Milestone 11B: controlled live-validation confirmation state -------

    def set_live_validation_state(
        self, *, publication_id: str, expected_etag: str, live_validation: dict
    ) -> tuple[ExecutionDocument, str]:
        """OCC-protected write of `metadata["live_validation"]` — the
        durable AWAITING_CONFIRMATION/CONFIRMED/CANCELLED record
        src/execution/live_validation.py uses instead of a wholly separate
        persistence domain (see that module's own docstring). Never
        touches `status`/`checkpoint`/`dispatch_authorization` — a
        controlled live-validation confirmation is a precondition *for*
        dispatch authorization, not dispatch authorization itself.
        Superseding an existing (even still-AWAITING_CONFIRMATION) record
        is intentional: a fresh preview always invalidates any prior,
        unconsumed confirmation token implicitly, since a caller can only
        ever compare against whatever is currently stored here."""

        loaded = self._repository.load(publication_id)
        document = loaded.document
        updated = replace(
            document, metadata={**document.metadata, "live_validation": live_validation}, updated_at=_now_iso(),
        )
        etag = self._repository.save(updated, expected_etag=expected_etag)
        logger.info(
            "Live-validation state persisted publication_id=%s status=%s",
            publication_id, live_validation.get("status"),
        )
        return updated, etag
