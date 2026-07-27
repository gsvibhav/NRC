"""Business logic for the active-workflow pointer: set, load, clear.

This is the only layer application code should call for conversation
state — it never exposes raw JSON or S3 details, and it owns timestamp
management and optimistic-concurrency validation, mirroring
src/workflow/manager.py.

Concurrency strategy: identical to WorkflowManager's — every write is a
single, atomic S3 PutObject of the entire document, so a torn write can
never replace a valid record with a corrupt one. `set_active_workflow` is
an upsert (a conversation record is long-lived across many workflows over
a user's history, unlike a workflow record, which is created once): it
first tries to load the existing record; if none exists, it creates one
with a conditional `IfNoneMatch="*"` write; if one exists, it updates it
carrying the ETag from that load, via `IfMatch=<etag>`, which raises
ConversationConcurrentModificationError if the record changed since. There
is no locking and no retry-on-conflict — a conflict is reported to the
caller.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone

from .errors import ConversationNotFoundError
from .models import ConversationDocument
from .repository import ConversationRepository
from .states import ConversationState

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationManager:
    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    def set_active_workflow(self, telegram_user_id: int, workflow_id: str) -> ConversationDocument:
        """Point this user's conversation at `workflow_id`, creating the
        conversation record on first use."""

        try:
            loaded = self._repository.load(telegram_user_id)
        except ConversationNotFoundError:
            document = ConversationDocument(
                telegram_user_id=telegram_user_id,
                active_workflow_id=workflow_id,
                state=ConversationState.ACTIVE,
                updated_at=_now_iso(),
            )
            self._repository.save(document, expected_etag=None)
            logger.info(
                "Active workflow set (new conversation) telegram_user_id=%s workflow_id=%s",
                telegram_user_id,
                workflow_id,
            )
            return document

        updated_document = replace(
            loaded.document,
            active_workflow_id=workflow_id,
            state=ConversationState.ACTIVE,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info(
            "Active workflow set telegram_user_id=%s workflow_id=%s", telegram_user_id, workflow_id
        )
        return updated_document

    def load_active_workflow(self, telegram_user_id: int) -> str | None:
        """Return the active workflow_id for this user, or None if the
        conversation exists but currently has no active pointer.

        Raises ConversationNotFoundError if no conversation record exists
        for this user at all (distinguished from "exists but idle" so
        resolution.py can log which case it was).
        """

        loaded = self._repository.load(telegram_user_id)
        logger.info(
            "Conversation loaded telegram_user_id=%s state=%s",
            telegram_user_id,
            loaded.document.state.value,
        )
        if loaded.document.state is not ConversationState.ACTIVE:
            return None
        return loaded.document.active_workflow_id

    def clear_active_workflow(self, telegram_user_id: int) -> ConversationDocument:
        """Clear the active workflow pointer for this user.

        Only the capability — callers decide when it's appropriate to
        invoke this (this milestone, only /cancel does). Raises
        ConversationNotFoundError if there's no conversation record to
        clear.
        """

        loaded = self._repository.load(telegram_user_id)
        updated_document = replace(
            loaded.document,
            active_workflow_id=None,
            state=ConversationState.IDLE,
            updated_at=_now_iso(),
        )
        self._repository.save(updated_document, expected_etag=loaded.etag)
        logger.info("Active workflow cleared telegram_user_id=%s", telegram_user_id)
        return updated_document
