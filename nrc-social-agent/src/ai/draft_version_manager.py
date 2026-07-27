"""Business logic for Milestone 7's immutable draft versions and the
current-version pointer: migration from Milestone 6's flat draft layout,
version-number allocation, and pointer-status transitions (approve / save
draft / reject).

Migration strategy (Option A from the brief): the very first time any
review action needs the "current" version and no `current.json` pointer
exists yet, `ensure_migrated()` loads the existing Milestone-6 flat draft
(`drafts/<workflow_id>/<output_id>.json`, via the caller-supplied
`legacy_draft`), copies it into an immutable version 1
(`drafts/<workflow_id>/<output_id>/versions/1.json`) with
`version_number=1`, `parent_version_number=None`, and creates
`current.json` pointing at it. The original flat object is never modified
or deleted — it simply becomes a historical duplicate of version 1, never
read again once `current.json` exists. Both new writes are conditional
creates, so this is idempotent and race-safe: if two callers race to
migrate the same workflow simultaneously, at most one wins each write, and
either way both callers end up reading back the same, single migrated
state (see `ensure_migrated()`'s docstring for the exact race handling).

Version-number allocation and orphaned versions: `create_next_version()`
reads the current pointer's version number and ETag, writes the candidate
next version as a brand-new immutable object (conditional create — can
only conflict if that exact version number was somehow already written,
e.g. a genuine double-processing bug), then attempts to advance the
pointer with `IfMatch=<the ETag just read>`. If a concurrent edit already
advanced the pointer in between (vanishingly rare for a single-user
Telegram conversation, but possible), that second write fails with
`DraftVersionConflictError` and the just-written version becomes
*orphaned*: a valid, immutable, permanently-unreferenced object. This is
deliberately left in place rather than deleted (no S3 deletion anywhere in
this pipeline) or renumbered (version numbers are never reused/shifted) —
it is simply inert, since every version-resolution path in this codebase
only ever reads through `current.json`, never lists or scans the
`versions/` namespace. The caller (draft_editing_service.py) does not
retry automatically; it surfaces the conflict, and the *next* attempt
re-reads the current pointer fresh and starts its own idempotency check
over again — see that module's module docstring.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from .draft_models import DraftDocument, DraftStatus
from .draft_version_models import CurrentDraftPointer, ReviewStatus
from .draft_version_repository import DraftVersionRepository
from .errors import (
    CurrentDraftPointerNotFoundError,
    DraftVersionConflictError,
    MissingCurrentDraftForEditError,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftVersionManager:
    def __init__(self, repository: DraftVersionRepository) -> None:
        self._repository = repository

    def get_current(self, workflow_id: str, output_id: str):
        """Return (DraftDocument, CurrentDraftPointer, pointer_etag) for the
        authoritative current version, or None if no pointer exists yet
        (migration not yet run, and no legacy draft to migrate either —
        callers that know a legacy draft exists should call
        ensure_migrated() instead of this)."""

        try:
            loaded_pointer = self._repository.load_pointer(workflow_id, output_id)
        except CurrentDraftPointerNotFoundError:
            return None

        loaded_version = self._repository.load_version(
            workflow_id, output_id, loaded_pointer.pointer.current_version_number
        )
        return loaded_version.document, loaded_pointer.pointer, loaded_pointer.etag

    def ensure_migrated(self, workflow_id: str, output_id: str, legacy_draft: DraftDocument):
        """Idempotent, race-safe migration of a Milestone-6 flat draft into
        Milestone-7's versioned layout. Returns (DraftDocument,
        CurrentDraftPointer, pointer_etag) for the resulting version 1,
        whether this call performed the migration or a concurrent/earlier
        call already had.

        Never deletes or mutates `legacy_draft`'s own flat object — this
        only ever creates new objects under the versions/ and current.json
        keys."""

        existing = self.get_current(workflow_id, output_id)
        if existing is not None:
            return existing

        version_one = DraftDocument(
            draft_id=legacy_draft.draft_id,
            workflow_id=legacy_draft.workflow_id,
            plan_id=legacy_draft.plan_id,
            output_id=legacy_draft.output_id,
            output_type=legacy_draft.output_type,
            created_at=legacy_draft.created_at,
            updated_at=legacy_draft.updated_at,
            status=DraftStatus.READY_FOR_REVIEW,
            schema_version=legacy_draft.schema_version,
            prompt_version=legacy_draft.prompt_version,
            model=legacy_draft.model,
            content=legacy_draft.content,
            source_versions=legacy_draft.source_versions,
            usage=legacy_draft.usage,
            metadata=legacy_draft.metadata,
            version_number=1,
            parent_version_number=None,
            edit_instruction_reference=None,
        )

        try:
            self._repository.save_version(version_one)
            logger.info(
                "Migrated legacy draft to version 1 workflow_id=%s output_id=%s draft_id=%s",
                workflow_id, output_id, version_one.draft_id,
            )
        except DraftVersionConflictError:
            # Someone else's migration attempt already created version 1 —
            # not an error, just a race we lost. Fall through to reading
            # back whatever now exists.
            logger.info(
                "Version 1 already migrated concurrently workflow_id=%s output_id=%s", workflow_id, output_id
            )

        pointer = CurrentDraftPointer(
            workflow_id=workflow_id,
            output_id=output_id,
            current_draft_id=version_one.draft_id,
            current_version_number=1,
            status=ReviewStatus.READY_FOR_REVIEW,
            updated_at=_now_iso(),
        )
        try:
            self._repository.save_pointer(pointer, expected_etag=None)
            logger.info("Current-version pointer created workflow_id=%s output_id=%s", workflow_id, output_id)
        except DraftVersionConflictError:
            logger.info(
                "Current-version pointer already created concurrently workflow_id=%s output_id=%s",
                workflow_id, output_id,
            )

        # Whichever attempt actually won each write, re-read to return a
        # single consistent view.
        result = self.get_current(workflow_id, output_id)
        assert result is not None  # unreachable: we either just created it or lost the race to someone who did
        return result

    def create_next_version(
        self,
        *,
        workflow_id: str,
        output_id: str,
        plan_id: str,
        output_type: str,
        parent_version_number: int,
        expected_pointer_etag: str,
        content: dict,
        model: str,
        schema_version: int,
        prompt_version: int,
        source_versions: dict | None,
        usage: dict | None,
        edit_instruction_reference: dict,
        metadata: dict | None = None,
    ) -> DraftDocument:
        """Create version `parent_version_number + 1` and advance the
        current pointer to it. The version write and the pointer write are
        two separate S3 requests (no cross-object transaction in S3), but
        both are conditional, and the pointer is only ever advanced
        *after* the version it points to already exists — so a reader can
        never observe `current.json` naming a version that doesn't exist
        yet.

        Raises DraftVersionConflictError if either write's precondition
        fails — see this module's docstring for what that means and how
        the caller should respond (report the conflict, do not blindly
        retry with a new candidate number)."""

        new_version_number = parent_version_number + 1
        now = _now_iso()
        new_version = DraftDocument(
            draft_id=uuid.uuid4().hex,
            workflow_id=workflow_id,
            plan_id=plan_id,
            output_id=output_id,
            output_type=output_type,
            created_at=now,
            updated_at=now,
            status=DraftStatus.READY_FOR_REVIEW,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=model,
            content=content,
            source_versions=source_versions,
            usage=usage,
            metadata=metadata or {},
            version_number=new_version_number,
            parent_version_number=parent_version_number,
            edit_instruction_reference=edit_instruction_reference,
        )

        # Conditional create — fails only if this exact version number was
        # already written (should be unreachable outside a genuine
        # double-processing bug, since version numbers are derived from a
        # freshly-read parent).
        self._repository.save_version(new_version)

        pointer = CurrentDraftPointer(
            workflow_id=workflow_id,
            output_id=output_id,
            current_draft_id=new_version.draft_id,
            current_version_number=new_version_number,
            status=ReviewStatus.READY_FOR_REVIEW,
            updated_at=now,
        )
        try:
            self._repository.save_pointer(pointer, expected_etag=expected_pointer_etag)
        except DraftVersionConflictError:
            logger.warning(
                "Current-version pointer advance lost a race workflow_id=%s output_id=%s "
                "orphaned_version=%s (version record itself is valid and untouched, just never became current)",
                workflow_id, output_id, new_version_number,
            )
            raise

        logger.info(
            "Draft version created and pointer advanced workflow_id=%s output_id=%s "
            "parent_version=%s new_version=%s",
            workflow_id, output_id, parent_version_number, new_version_number,
        )
        return new_version

    def update_pointer_status(
        self,
        *,
        workflow_id: str,
        output_id: str,
        status: ReviewStatus,
        approved_at: str | None = None,
        approved_by_telegram_user_id: int | None = None,
    ) -> CurrentDraftPointer:
        """Mutate only the current pointer's review-lifecycle status (and,
        for an approval, its approval metadata) — never the immutable
        version it names. Used by approve/save/reject; never by the
        editing path, which advances the pointer to a *new* version
        instead via create_next_version().

        Unlike create_next_version() (which is handed an etag captured
        *before* a Claude call, deliberately, so the OCC check spans that
        whole transaction), this always reloads the pointer fresh
        immediately before writing — there's no intervening long-running
        step here to protect against, so the simpler load-then-save
        pattern (matching WorkflowManager's own methods) is the right
        level of care."""

        loaded = self._repository.load_pointer(workflow_id, output_id)
        updated = replace(
            loaded.pointer,
            status=status,
            updated_at=_now_iso(),
            approved_at=approved_at if approved_at is not None else loaded.pointer.approved_at,
            approved_by_telegram_user_id=(
                approved_by_telegram_user_id
                if approved_by_telegram_user_id is not None
                else loaded.pointer.approved_by_telegram_user_id
            ),
        )
        self._repository.save_pointer(updated, expected_etag=loaded.etag)
        logger.info(
            "Current-version pointer status updated workflow_id=%s output_id=%s status=%s",
            workflow_id, output_id, status.value,
        )
        return updated


def resolve_current_draft(*, draft_version_manager: DraftVersionManager, draft_manager, workflow_id: str, output_id: str):
    """Shared entry point for "get the authoritative current draft,
    migrating from the Milestone-6 flat layout if this is the first time
    anyone has needed it" — used identically by
    draft_editing_service.py, handlers.py's review_action(), and /status,
    so the migration trigger point lives in exactly one place rather than
    being re-implemented at each call site.

    Returns (DraftDocument, CurrentDraftPointer, pointer_etag). Raises
    MissingCurrentDraftForEditError if neither a versioned current pointer
    nor a legacy Milestone-6 flat draft exists — a data-consistency
    signal, should be unreachable once a workflow has a `generated_draft`
    reference at all."""

    existing = draft_version_manager.get_current(workflow_id, output_id)
    if existing is not None:
        return existing

    legacy_draft = draft_manager.find_existing(workflow_id, output_id)
    if legacy_draft is None:
        raise MissingCurrentDraftForEditError(
            f"no versioned or legacy draft found for workflow_id={workflow_id} output_id={output_id}"
        )

    return draft_version_manager.ensure_migrated(workflow_id, output_id, legacy_draft)
