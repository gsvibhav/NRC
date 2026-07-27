"""Business logic for draft persistence: create, look up, and — the one
explicit exception — supersede a failed attempt.

Mirrors src/ai/analysis_manager.py / content_plan_manager.py exactly,
keyed by `(workflow_id, output_id)` instead of `workflow_id` alone (one
workflow could, in a future milestone, have drafts for more than one
output — this milestone only ever generates the single priority-1
output, but the key shape doesn't foreclose that later).

- create_draft() uses a conditional create (`IfNoneMatch="*"`) — a
  READY_FOR_REVIEW draft is never silently overwritten by a second
  attempt.
- supersede_failed_draft() is the one place an existing document is
  replaced: it carries the ETag from the load that found the FAILED
  record, so two concurrent retry attempts can't both silently win.

No locking, no automatic retry-on-conflict — a conflict is reported to
the caller (draft_generation_service.py).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .draft_repository import DraftRepository
from .draft_models import DraftDocument, DraftStatus
from .errors import DraftNotFoundError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftManager:
    def __init__(self, repository: DraftRepository) -> None:
        self._repository = repository

    def find_existing(self, workflow_id: str, output_id: str) -> DraftDocument | None:
        """Return the existing draft for this workflow+output, or None if
        none has ever been created. This is the idempotency check callers
        must run before invoking Claude."""

        try:
            loaded = self._repository.load(workflow_id, output_id)
        except DraftNotFoundError:
            return None
        return loaded.document

    def create_draft(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        output_id: str,
        output_type: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        status: DraftStatus,
        content: dict | None = None,
        source_versions: dict | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
    ) -> DraftDocument:
        """Create and persist a new draft. Raises
        DraftConcurrentModificationError if one already exists for this
        workflow_id+output_id — see supersede_failed_draft() to replace an
        existing FAILED record instead."""

        now = _now_iso()
        document = DraftDocument(
            draft_id=uuid.uuid4().hex,
            workflow_id=workflow_id,
            plan_id=plan_id,
            output_id=output_id,
            output_type=output_type,
            created_at=now,
            updated_at=now,
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=model,
            content=content,
            source_versions=source_versions,
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(document, expected_etag=None)
        logger.info(
            "Draft created workflow_id=%s output_id=%s draft_id=%s status=%s",
            workflow_id, output_id, document.draft_id, status.value,
        )
        return document

    def supersede_failed_draft(
        self,
        *,
        workflow_id: str,
        output_id: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        status: DraftStatus,
        content: dict | None = None,
        source_versions: dict | None = None,
        usage: dict | None = None,
        metadata: dict | None = None,
    ) -> DraftDocument:
        """Replace an existing FAILED draft with a fresh attempt, using
        the loaded record's ETag for optimistic concurrency."""

        loaded = self._repository.load(workflow_id, output_id)
        updated = replace(
            loaded.document,
            updated_at=_now_iso(),
            status=status,
            model=model,
            schema_version=schema_version,
            prompt_version=prompt_version,
            content=content,
            source_versions=source_versions,
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(updated, expected_etag=loaded.etag)
        logger.info(
            "Draft superseded workflow_id=%s output_id=%s draft_id=%s status=%s",
            workflow_id, output_id, updated.draft_id, status.value,
        )
        return updated
