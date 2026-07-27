"""Repository abstraction: the only place that translates between
AnalysisDocument objects and the underlying S3-backed store. Documents are
keyed by workflow_id — one analysis document per workflow (see
analysis_manager.py for what that implies for retries/idempotency).

Mirrors src/workflow/repository.py and src/conversation/repository.py
exactly. Business logic (AnalysisManager) never sees raw JSON or S3
exceptions — only AnalysisDocument objects and the errors in errors.py.
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
from .analysis_store import AnalysisStore
from .errors import (
    AnalysisConcurrentModificationError,
    AnalysisDeserializationError,
    AnalysisNotFoundError,
    AnalysisPersistenceError,
    AnalysisSerializationError,
)
from .models import AnalysisDocument


@dataclass(frozen=True)
class LoadedAnalysis:
    document: AnalysisDocument
    etag: str


class AnalysisRepository:
    def __init__(self, store: AnalysisStore) -> None:
        self._store = store

    def exists(self, workflow_id: str) -> bool:
        try:
            return self._store.exists(workflow_id)
        except ObjectStoreError as exc:
            raise AnalysisPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, workflow_id: str) -> LoadedAnalysis:
        try:
            data, etag = self._store.read(workflow_id)
        except ObjectNotFoundError as exc:
            raise AnalysisNotFoundError(f"workflow_id={workflow_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise AnalysisDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise AnalysisPersistenceError(f"failed to read workflow_id={workflow_id}: {exc}") from exc

        document = AnalysisDocument.from_dict(data)
        return LoadedAnalysis(document=document, etag=etag)

    def save(self, document: AnalysisDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        analysis (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        data = document.to_dict()
        try:
            return self._store.write(document.workflow_id, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise AnalysisConcurrentModificationError(
                f"workflow_id={document.workflow_id} was modified concurrently"
            ) from exc
        except ObjectSerializationError as exc:
            raise AnalysisSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise AnalysisPersistenceError(f"failed to write workflow_id={document.workflow_id}: {exc}") from exc
