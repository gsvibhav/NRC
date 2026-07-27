"""Repository abstraction for Milestone 7's immutable draft versions and
current-version pointer.

Reuses the existing `src/workflow/draft_store.py`'s `DraftStore` for a
*third* distinct key shape sharing the `drafts/` prefix — continuing the
precedent already established twice (the reserved Milestone-3
`drafts/<workflow_id>.json` "Save Draft" key, and Milestone 6's
`drafts/<workflow_id>/<output_id>.json` flat draft key):

    drafts/<workflow_id>/<output_id>/versions/<version_number>.json   (immutable)
    drafts/<workflow_id>/<output_id>/current.json                     (mutable pointer)

`JsonObjectStore`'s generic `_build_key()` (`f"{prefix}/{document_id}.json"`)
already produces exactly these paths given composite `document_id` strings
— no store-class changes needed, exactly as before.

Placed under `src/ai/` (like draft_repository.py), mirroring the same
placement decision already made and flagged twice.
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
from ..workflow.draft_store import DraftStore
from .draft_models import DraftDocument
from .draft_version_models import CurrentDraftPointer
from .errors import (
    CurrentDraftPointerNotFoundError,
    DraftVersionConflictError,
    DraftVersionDeserializationError,
    DraftVersionNotFoundError,
    DraftVersionPersistenceError,
    DraftVersionSerializationError,
)


def _version_document_id(workflow_id: str, output_id: str, version_number: int) -> str:
    return f"{workflow_id}/{output_id}/versions/{version_number}"


def _current_document_id(workflow_id: str, output_id: str) -> str:
    return f"{workflow_id}/{output_id}/current"


@dataclass(frozen=True)
class LoadedDraftVersion:
    document: DraftDocument
    etag: str


@dataclass(frozen=True)
class LoadedCurrentPointer:
    pointer: CurrentDraftPointer
    etag: str


class DraftVersionRepository:
    def __init__(self, store: DraftStore) -> None:
        self._store = store

    # --- immutable versions ------------------------------------------------

    def version_exists(self, workflow_id: str, output_id: str, version_number: int) -> bool:
        try:
            return self._store.exists(_version_document_id(workflow_id, output_id, version_number))
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to check version existence: {exc}") from exc

    def load_version(self, workflow_id: str, output_id: str, version_number: int) -> LoadedDraftVersion:
        try:
            data, etag = self._store.read(_version_document_id(workflow_id, output_id, version_number))
        except ObjectNotFoundError as exc:
            raise DraftVersionNotFoundError(
                f"workflow_id={workflow_id} output_id={output_id} version_number={version_number} not found"
            ) from exc
        except ObjectDeserializationError as exc:
            raise DraftVersionDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to read version: {exc}") from exc

        return LoadedDraftVersion(document=DraftDocument.from_dict(data), etag=etag)

    def save_version(self, document: DraftDocument) -> str:
        """Persist a brand-new immutable version. Always a conditional
        create (`IfNoneMatch="*"`) — versions are never updated once
        written, so there is no `expected_etag` parameter at all, unlike
        every other repository in this codebase. Raises
        DraftVersionConflictError if a version already exists at this
        exact (workflow_id, output_id, version_number) — see
        draft_version_manager.py for what that means and how it's
        handled."""

        key = _version_document_id(document.workflow_id, document.output_id, document.version_number)
        data = document.to_dict()
        try:
            return self._store.write(key, data, expected_etag=None)
        except ObjectConcurrentModificationError as exc:
            raise DraftVersionConflictError(f"version already exists for key={key}") from exc
        except ObjectSerializationError as exc:
            raise DraftVersionSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to write version for key={key}: {exc}") from exc

    # --- current-version pointer --------------------------------------------

    def pointer_exists(self, workflow_id: str, output_id: str) -> bool:
        try:
            return self._store.exists(_current_document_id(workflow_id, output_id))
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to check pointer existence: {exc}") from exc

    def load_pointer(self, workflow_id: str, output_id: str) -> LoadedCurrentPointer:
        try:
            data, etag = self._store.read(_current_document_id(workflow_id, output_id))
        except ObjectNotFoundError as exc:
            raise CurrentDraftPointerNotFoundError(
                f"no current-version pointer for workflow_id={workflow_id} output_id={output_id}"
            ) from exc
        except ObjectDeserializationError as exc:
            raise DraftVersionDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to read current pointer: {exc}") from exc

        return LoadedCurrentPointer(pointer=CurrentDraftPointer.from_dict(data), etag=etag)

    def save_pointer(self, pointer: CurrentDraftPointer, *, expected_etag: str | None = None) -> str:
        """Persist the current-version pointer. Pass expected_etag=None
        only to create it for the first time (fails if one already
        exists); pass the ETag from a prior load() to update it (fails if
        it changed since — see draft_version_manager.py's OCC strategy).
        Raises DraftVersionConflictError on either kind of conflict."""

        key = _current_document_id(pointer.workflow_id, pointer.output_id)
        data = pointer.to_dict()
        try:
            return self._store.write(key, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise DraftVersionConflictError(f"current pointer for key={key} was modified concurrently") from exc
        except ObjectSerializationError as exc:
            raise DraftVersionSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftVersionPersistenceError(f"failed to write current pointer for key={key}: {exc}") from exc
