"""Repository abstraction: the only place that translates between
ConversationDocument objects and the underlying S3-backed store.

Mirrors src/workflow/repository.py exactly. Business logic
(ConversationManager) never sees raw JSON or S3 exceptions — only
ConversationDocument objects and the errors declared in errors.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..storage.json_object_store import (
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)
from .errors import (
    ConversationConcurrentModificationError,
    ConversationDeserializationError,
    ConversationNotFoundError,
    ConversationPersistenceError,
    ConversationSerializationError,
)
from .models import ConversationDocument
from .store import ConversationStore


@dataclass(frozen=True)
class LoadedConversation:
    """A ConversationDocument plus the ETag it was loaded with.

    The ETag is an S3 mechanic, not a domain concept — it never appears in
    ConversationDocument.to_dict()/from_dict(). It travels only as far as
    the next save() call, entirely inside ConversationManager (see
    manager.py).
    """

    document: ConversationDocument
    etag: str


class ConversationRepository:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def load(self, telegram_user_id: int) -> LoadedConversation:
        document_id = str(telegram_user_id)
        try:
            data, etag = self._store.read(document_id)
        except ObjectNotFoundError as exc:
            raise ConversationNotFoundError(f"telegram_user_id={telegram_user_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise ConversationDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ConversationPersistenceError(
                f"failed to read telegram_user_id={telegram_user_id}: {exc}"
            ) from exc

        document = ConversationDocument.from_dict(data)
        return LoadedConversation(document=document, etag=etag)

    def save(self, document: ConversationDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        conversation record (fails if one already exists); pass the ETag
        from a prior load() to update it (fails if it changed since)."""

        document_id = str(document.telegram_user_id)
        data = document.to_dict()
        try:
            return self._store.write(document_id, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise ConversationConcurrentModificationError(
                f"telegram_user_id={document.telegram_user_id} was modified concurrently"
            ) from exc
        except ObjectSerializationError as exc:
            raise ConversationSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ConversationPersistenceError(
                f"failed to write telegram_user_id={document.telegram_user_id}: {exc}"
            ) from exc
