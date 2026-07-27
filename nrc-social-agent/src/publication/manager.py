"""Business logic for publication-package persistence: create, look up,
and — the one explicit exception — supersede a failed attempt.

Mirrors src/ai/draft_manager.py exactly:

- create_publication() uses a conditional create (`IfNoneMatch="*"`) — a
  READY_FOR_PUBLISHING package is never silently overwritten by a second
  attempt.
- supersede_failed_publication() is the one place an existing document is
  replaced: it carries the ETag from the load that found the FAILED
  record, so two concurrent retry attempts can't both silently win.

No locking, no automatic retry-on-conflict — a conflict is reported to
the caller (service.py).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .errors import PublicationNotFoundError
from .models import PublicationChannel, PublicationPackage, PublicationStatus
from .repository import PublicationRepository

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PublicationManager:
    def __init__(self, repository: PublicationRepository) -> None:
        self._repository = repository

    def find_existing(self, workflow_id: str, output_id: str) -> PublicationPackage | None:
        """Return the existing package for this workflow+output, or None
        if none has ever been created. This is the idempotency check
        callers must run before building a new package."""

        try:
            loaded = self._repository.load(workflow_id, output_id)
        except PublicationNotFoundError:
            return None
        return loaded.document

    def create_publication(
        self,
        *,
        workflow_id: str,
        plan_id: str,
        output_id: str,
        output_type: str,
        channel: PublicationChannel,
        schema_version: int,
        status: PublicationStatus,
        draft: dict | None = None,
        content: dict | None = None,
        media: list | None = None,
        approval: dict | None = None,
        source_versions: dict | None = None,
        placement: dict | None = None,
        metadata: dict | None = None,
    ) -> PublicationPackage:
        """Create and persist a new publication package. Raises
        PublicationConcurrentModificationError if one already exists for
        this workflow_id+output_id — see supersede_failed_publication() to
        replace an existing FAILED record instead."""

        now = _now_iso()
        document = PublicationPackage(
            publication_id=f"pub_{uuid.uuid4().hex}",
            workflow_id=workflow_id,
            plan_id=plan_id,
            output_id=output_id,
            output_type=output_type,
            channel=channel,
            status=status,
            schema_version=schema_version,
            created_at=now,
            updated_at=now,
            draft=draft,
            content=content,
            media=media or [],
            approval=approval,
            source_versions=source_versions,
            placement=placement,
            metadata=metadata or {},
        )
        self._repository.save(document, expected_etag=None)
        logger.info(
            "Publication package created workflow_id=%s output_id=%s publication_id=%s status=%s",
            workflow_id, output_id, document.publication_id, status.value,
        )
        return document

    def supersede_failed_publication(
        self,
        *,
        workflow_id: str,
        output_id: str,
        channel: PublicationChannel,
        schema_version: int,
        status: PublicationStatus,
        draft: dict | None = None,
        content: dict | None = None,
        media: list | None = None,
        approval: dict | None = None,
        source_versions: dict | None = None,
        placement: dict | None = None,
        metadata: dict | None = None,
    ) -> PublicationPackage:
        """Replace an existing FAILED publication record with a fresh
        attempt, using the loaded record's ETag for optimistic
        concurrency."""

        loaded = self._repository.load(workflow_id, output_id)
        updated = replace(
            loaded.document,
            updated_at=_now_iso(),
            channel=channel,
            status=status,
            schema_version=schema_version,
            draft=draft,
            content=content,
            media=media or [],
            approval=approval,
            source_versions=source_versions,
            placement=placement,
            metadata=metadata or {},
        )
        self._repository.save(updated, expected_etag=loaded.etag)
        logger.info(
            "Publication package superseded workflow_id=%s output_id=%s publication_id=%s status=%s",
            workflow_id, output_id, updated.publication_id, status.value,
        )
        return updated
