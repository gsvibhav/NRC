"""Repository abstraction: the only place that translates between
WorkflowDocument objects and the underlying S3-backed store.

Responsible only for persistence (read / write / update / existence check).
Business logic (WorkflowManager) never sees raw JSON or S3 exceptions —
only WorkflowDocument objects and the errors declared in errors.py.
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
    WorkflowConcurrentModificationError,
    WorkflowDeserializationError,
    WorkflowNotFoundError,
    WorkflowPersistenceError,
    WorkflowSerializationError,
)
from .models import WorkflowDocument
from .state_store import WorkflowStateStore


@dataclass(frozen=True)
class LoadedWorkflow:
    """A WorkflowDocument plus the ETag it was loaded with.

    The ETag is an S3 mechanic, not a domain concept — it never appears in
    WorkflowDocument.to_dict()/from_dict(). It travels only as far as the
    next save() call, entirely inside WorkflowManager (see manager.py).
    """

    document: WorkflowDocument
    etag: str


class WorkflowRepository:
    def __init__(self, store: WorkflowStateStore) -> None:
        self._store = store

    def exists(self, workflow_id: str) -> bool:
        try:
            return self._store.exists(workflow_id)
        except ObjectStoreError as exc:
            raise WorkflowPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, workflow_id: str) -> LoadedWorkflow:
        try:
            data, etag = self._store.read(workflow_id)
        except ObjectNotFoundError as exc:
            raise WorkflowNotFoundError(f"workflow_id={workflow_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise WorkflowDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise WorkflowPersistenceError(f"failed to read workflow_id={workflow_id}: {exc}") from exc

        document = WorkflowDocument.from_dict(data)
        return LoadedWorkflow(document=document, etag=etag)

    def save(self, document: WorkflowDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        workflow (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        data = document.to_dict()
        try:
            return self._store.write(document.workflow_id, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise WorkflowConcurrentModificationError(
                f"workflow_id={document.workflow_id} was modified concurrently"
            ) from exc
        except ObjectSerializationError as exc:
            raise WorkflowSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise WorkflowPersistenceError(f"failed to write workflow_id={document.workflow_id}: {exc}") from exc
