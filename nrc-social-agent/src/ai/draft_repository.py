"""Repository abstraction: the only place that translates between
DraftDocument objects and the underlying S3-backed store.

Reuses the existing `src/workflow/draft_store.py`'s `DraftStore` (reserved
since Milestone 3, fixed to the `drafts/` prefix) rather than creating a
new store class — this milestone's key is a composite
`<workflow_id>/<output_id>` string, which `JsonObjectStore`'s generic
`_build_key()` (`f"{prefix}/{document_id}.json"`) already turns into
exactly `drafts/<workflow_id>/<output_id>.json` with no changes needed to
the store itself. Placed under `src/ai/` (like analysis_repository.py and
content_plan_repository.py) since it deals in the AI-pipeline's
`DraftDocument` dataclass, even though the underlying store class lives in
`src/workflow/` — flagged in the completion report as a placement
decision, mirroring Milestone 4A's identical call for the analysis
persistence layer.
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
from .errors import (
    DraftConcurrentModificationError,
    DraftDeserializationError,
    DraftNotFoundError,
    DraftPersistenceError,
    DraftSerializationError,
)


def _document_id(workflow_id: str, output_id: str) -> str:
    return f"{workflow_id}/{output_id}"


@dataclass(frozen=True)
class LoadedDraft:
    document: DraftDocument
    etag: str


class DraftRepository:
    def __init__(self, store: DraftStore) -> None:
        self._store = store

    def exists(self, workflow_id: str, output_id: str) -> bool:
        try:
            return self._store.exists(_document_id(workflow_id, output_id))
        except ObjectStoreError as exc:
            raise DraftPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, workflow_id: str, output_id: str) -> LoadedDraft:
        try:
            data, etag = self._store.read(_document_id(workflow_id, output_id))
        except ObjectNotFoundError as exc:
            raise DraftNotFoundError(f"workflow_id={workflow_id} output_id={output_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise DraftDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftPersistenceError(
                f"failed to read workflow_id={workflow_id} output_id={output_id}: {exc}"
            ) from exc

        document = DraftDocument.from_dict(data)
        return LoadedDraft(document=document, etag=etag)

    def save(self, document: DraftDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        draft (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        key = _document_id(document.workflow_id, document.output_id)
        data = document.to_dict()
        try:
            return self._store.write(key, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise DraftConcurrentModificationError(f"draft for key={key} was modified concurrently") from exc
        except ObjectSerializationError as exc:
            raise DraftSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise DraftPersistenceError(f"failed to write draft for key={key}: {exc}") from exc
