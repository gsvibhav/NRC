"""Business logic for content-plan persistence: create, look up, and — the
one explicit exception — supersede a failed attempt.

Mirrors src/ai/analysis_manager.py exactly, including its concurrency
strategy:

- create_content_plan() uses a conditional create (`IfNoneMatch="*"`) — a
  COMPLETED plan is never silently overwritten by a second attempt.
- supersede_failed_content_plan() is the one place an existing document is
  replaced: it carries the ETag from the load that found the FAILED
  record, so two concurrent retry attempts can't both silently win — the
  second one raises ContentPlanConcurrentModificationError.

There is no locking and no automatic retry-on-conflict — a conflict is
reported to the caller (content_planning_service.py) rather than silently
resolved.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .content_plan_repository import ContentPlanRepository
from .content_plan_models import ContentPlanDocument, ContentPlanStatus
from .errors import ContentPlanNotFoundError, ContentPlanSchemaMismatchError

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContentPlanManager:
    def __init__(self, repository: ContentPlanRepository) -> None:
        self._repository = repository

    def find_existing(self, workflow_id: str) -> ContentPlanDocument | None:
        """Return the existing content plan for this workflow, or None if
        none has ever been created. This is the idempotency check callers
        must run before invoking Claude."""

        try:
            loaded = self._repository.load(workflow_id)
        except ContentPlanNotFoundError:
            return None
        return loaded.document

    def create_content_plan(
        self,
        *,
        workflow_id: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        status: ContentPlanStatus,
        strategy=None,
        outputs=None,
        excluded_outputs=None,
        usage=None,
        metadata: dict | None = None,
    ) -> ContentPlanDocument:
        """Create and persist a new content plan. Raises
        ContentPlanConcurrentModificationError if one already exists for
        this workflow_id — see supersede_failed_content_plan() to replace
        an existing FAILED record instead."""

        now = _now_iso()
        document = ContentPlanDocument(
            plan_id=uuid.uuid4().hex,
            workflow_id=workflow_id,
            created_at=now,
            updated_at=now,
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=model,
            strategy=strategy,
            outputs=outputs or [],
            excluded_outputs=excluded_outputs or [],
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(document, expected_etag=None)
        logger.info(
            "Content plan created workflow_id=%s plan_id=%s status=%s",
            workflow_id, document.plan_id, status.value,
        )
        return document

    def supersede_failed_content_plan(
        self,
        *,
        workflow_id: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        status: ContentPlanStatus,
        strategy=None,
        outputs=None,
        excluded_outputs=None,
        usage=None,
        metadata: dict | None = None,
    ) -> ContentPlanDocument:
        """Replace an existing content-plan record with a fresh attempt,
        using the loaded record's ETag for optimistic concurrency. Raises
        ContentPlanConcurrentModificationError if the record changed since
        it was loaded (e.g. a concurrent attempt already won)."""

        loaded = self._repository.load(workflow_id)
        updated = replace(
            loaded.document,
            updated_at=_now_iso(),
            status=status,
            model=model,
            schema_version=schema_version,
            prompt_version=prompt_version,
            strategy=strategy,
            outputs=outputs or [],
            excluded_outputs=excluded_outputs or [],
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(updated, expected_etag=loaded.etag)
        logger.info(
            "Content plan superseded workflow_id=%s plan_id=%s status=%s",
            workflow_id, updated.plan_id, status.value,
        )
        return updated

    def mark_output_generated(self, *, workflow_id: str, output_id: str) -> ContentPlanDocument:
        """Flip exactly one output's `generation_status` from its default
        `NOT_STARTED` to `GENERATED` (see PlannedOutput.generation_status,
        content_plan_models.py) — called only once by
        draft_generation_service.py, after a draft has actually been
        persisted as READY_FOR_REVIEW, so this always reflects reality
        rather than an attempt in progress. Uses the loaded record's ETag
        for optimistic concurrency, exactly like the other update methods
        here.

        This is informational bookkeeping on the plan, not the source of
        generation idempotency — that's DraftManager.find_existing()
        against the drafts/ store, checked before any Claude call. Raises
        ContentPlanSchemaMismatchError if output_id isn't among the plan's
        outputs (should be unreachable given how callers obtain
        output_id)."""

        loaded = self._repository.load(workflow_id)
        updated_outputs = []
        found = False
        for output in loaded.document.outputs:
            if output.output_id == output_id:
                updated_outputs.append(replace(output, generation_status="GENERATED"))
                found = True
            else:
                updated_outputs.append(output)
        if not found:
            raise ContentPlanSchemaMismatchError(
                f"output_id={output_id} not found among plan outputs for workflow_id={workflow_id}"
            )

        updated_document = replace(loaded.document, outputs=updated_outputs, updated_at=_now_iso())
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info(
            "Content plan output marked generated workflow_id=%s output_id=%s", workflow_id, output_id,
        )
        return updated_document
