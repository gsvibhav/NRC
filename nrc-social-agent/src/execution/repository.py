"""Repository abstraction: the only place that translates between
ExecutionDocument objects and the underlying S3-backed store.

Keyed by `publication_id` alone — deliberately the flat
`executions/<publication_id>.json` layout (of the two the brief allows),
not nested under `workflow_id`. `publication_id` is already a globally
unique join key (a random `pub_<uuid4().hex>`, see
src/publication/manager.py), so no further disambiguation is needed —
unlike drafts/publications, which key on `(workflow_id, output_id)`
because a single workflow can have multiple outputs. One publication
package maps to at most one execution record, full stop.
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
    ExecutionConcurrentModificationError,
    ExecutionDeserializationError,
    ExecutionNotFoundError,
    ExecutionPersistenceError,
    ExecutionSerializationError,
)
from .models import ExecutionDocument
from .store import ExecutionStore


@dataclass(frozen=True)
class LoadedExecution:
    document: ExecutionDocument
    etag: str


class ExecutionRepository:
    def __init__(self, store: ExecutionStore) -> None:
        self._store = store

    def exists(self, publication_id: str) -> bool:
        try:
            return self._store.exists(publication_id)
        except ObjectStoreError as exc:
            raise ExecutionPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, publication_id: str) -> LoadedExecution:
        try:
            data, etag = self._store.read(publication_id)
        except ObjectNotFoundError as exc:
            raise ExecutionNotFoundError(f"publication_id={publication_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise ExecutionDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ExecutionPersistenceError(
                f"failed to read publication_id={publication_id}: {exc}"
            ) from exc

        return LoadedExecution(document=ExecutionDocument.from_dict(data), etag=etag)

    def save(self, document: ExecutionDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        record (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        key = document.publication_id
        data = document.to_dict()
        try:
            return self._store.write(key, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise ExecutionConcurrentModificationError(
                f"execution record for publication_id={key} was modified concurrently"
            ) from exc
        except ObjectSerializationError as exc:
            raise ExecutionSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ExecutionPersistenceError(f"failed to write execution record for publication_id={key}: {exc}") from exc
