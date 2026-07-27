"""Repository abstraction: the only place that translates between
PublicationPackage objects and the underlying S3-backed store.

Keyed by `(workflow_id, output_id)` — one authoritative publication
package per approved output, exactly mirroring src/ai/draft_manager.py's
identical keying rationale (Milestone 6): a composite `document_id`
string passed through `JsonObjectStore`'s generic `_build_key()`
(`f"{prefix}/{document_id}.json"`) already produces exactly
`publications/<workflow_id>/<output_id>.json` with no store-class changes
needed. This also gives deterministic, restart-safe lookup and idempotency
"for free" — the S3 key itself, not the (randomly-generated)
`publication_id`, is the authoritative identity for "does a package
already exist for this approved output."
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
    PublicationConcurrentModificationError,
    PublicationDeserializationError,
    PublicationNotFoundError,
    PublicationPersistenceError,
    PublicationSerializationError,
)
from .models import PublicationPackage
from .store import PublicationStore


def _document_id(workflow_id: str, output_id: str) -> str:
    return f"{workflow_id}/{output_id}"


@dataclass(frozen=True)
class LoadedPublication:
    document: PublicationPackage
    etag: str


class PublicationRepository:
    def __init__(self, store: PublicationStore) -> None:
        self._store = store

    def exists(self, workflow_id: str, output_id: str) -> bool:
        try:
            return self._store.exists(_document_id(workflow_id, output_id))
        except ObjectStoreError as exc:
            raise PublicationPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, workflow_id: str, output_id: str) -> LoadedPublication:
        try:
            data, etag = self._store.read(_document_id(workflow_id, output_id))
        except ObjectNotFoundError as exc:
            raise PublicationNotFoundError(
                f"workflow_id={workflow_id} output_id={output_id} not found"
            ) from exc
        except ObjectDeserializationError as exc:
            raise PublicationDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise PublicationPersistenceError(
                f"failed to read workflow_id={workflow_id} output_id={output_id}: {exc}"
            ) from exc

        return LoadedPublication(document=PublicationPackage.from_dict(data), etag=etag)

    def save(self, document: PublicationPackage, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        package (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        key = _document_id(document.workflow_id, document.output_id)
        data = document.to_dict()
        try:
            return self._store.write(key, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise PublicationConcurrentModificationError(f"package for key={key} was modified concurrently") from exc
        except ObjectSerializationError as exc:
            raise PublicationSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise PublicationPersistenceError(f"failed to write package for key={key}: {exc}") from exc
