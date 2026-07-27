"""Business logic for analysis persistence: create, look up, and — the one
explicit exception — supersede a failed attempt.

Concurrency strategy: identical to WorkflowManager/ConversationManager —
every write is a single atomic S3 PutObject, so a torn write can never
replace a valid document with a corrupt one. On top of that:

- create_analysis() uses a conditional create (`IfNoneMatch="*"`) — a
  COMPLETED analysis is never silently overwritten by a second attempt.
- supersede_failed_analysis() is the one place an existing document is
  replaced: it carries the ETag from the load that found the FAILED
  record, so two concurrent retry attempts can't both silently win — the
  second one raises AnalysisConcurrentModificationError.

There is no locking and no automatic retry-on-conflict — a conflict is
reported to the caller (src/ai/analysis_service.py) rather than silently
resolved.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .analysis_repository import AnalysisRepository
from .errors import AnalysisNotFoundError
from .models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisManager:
    def __init__(self, repository: AnalysisRepository) -> None:
        self._repository = repository

    def find_existing(self, workflow_id: str) -> AnalysisDocument | None:
        """Return the existing analysis document for this workflow, or
        None if none has ever been created. This is the idempotency check
        callers must run before invoking Claude."""

        try:
            loaded = self._repository.load(workflow_id)
        except AnalysisNotFoundError:
            return None
        return loaded.document

    def create_analysis(
        self,
        *,
        workflow_id: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        media_type: str,
        status: AnalysisStatus,
        result: AnalysisResult | None = None,
        usage: AnalysisUsage | None = None,
        metadata: dict | None = None,
    ) -> AnalysisDocument:
        """Create and persist a new analysis record. Raises
        AnalysisConcurrentModificationError if one already exists for this
        workflow_id — see supersede_failed_analysis() to replace an
        existing FAILED record instead."""

        now = _now_iso()
        document = AnalysisDocument(
            analysis_id=uuid.uuid4().hex,
            workflow_id=workflow_id,
            created_at=now,
            updated_at=now,
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=model,
            media_type=media_type,
            result=result,
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(document, expected_etag=None)
        logger.info(
            "Analysis created workflow_id=%s analysis_id=%s status=%s",
            workflow_id,
            document.analysis_id,
            status.value,
        )
        return document

    def supersede_failed_analysis(
        self,
        *,
        workflow_id: str,
        model: str,
        schema_version: int,
        prompt_version: int,
        media_type: str,
        status: AnalysisStatus,
        result: AnalysisResult | None = None,
        usage: AnalysisUsage | None = None,
        metadata: dict | None = None,
    ) -> AnalysisDocument:
        """Replace an existing analysis record with a fresh attempt, using
        the loaded record's ETag for optimistic concurrency. Raises
        AnalysisConcurrentModificationError if the record changed since it
        was loaded (e.g. a concurrent attempt already won)."""

        loaded = self._repository.load(workflow_id)
        updated = replace(
            loaded.document,
            updated_at=_now_iso(),
            status=status,
            model=model,
            schema_version=schema_version,
            prompt_version=prompt_version,
            media_type=media_type,
            result=result,
            usage=usage,
            metadata=metadata or {},
        )
        self._repository.save(updated, expected_etag=loaded.etag)
        logger.info(
            "Analysis superseded workflow_id=%s analysis_id=%s status=%s",
            workflow_id,
            updated.analysis_id,
            status.value,
        )
        return updated
