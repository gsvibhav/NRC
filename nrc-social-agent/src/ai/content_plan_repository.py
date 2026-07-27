"""Repository abstraction: the only place that translates between
ContentPlanDocument objects and the underlying S3-backed store. Documents
are keyed by workflow_id — one content plan per workflow (see
content_plan_manager.py for what that implies for retries/idempotency).

Mirrors src/ai/analysis_repository.py exactly. Business logic
(ContentPlanManager) never sees raw JSON or S3 exceptions — only
ContentPlanDocument objects and the errors in errors.py.
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
from .content_plan_store import ContentPlanStore
from .content_plan_models import ContentPlanDocument
from .errors import (
    ContentPlanConcurrentModificationError,
    ContentPlanDeserializationError,
    ContentPlanNotFoundError,
    ContentPlanPersistenceError,
    ContentPlanSerializationError,
)


@dataclass(frozen=True)
class LoadedContentPlan:
    document: ContentPlanDocument
    etag: str


class ContentPlanRepository:
    def __init__(self, store: ContentPlanStore) -> None:
        self._store = store

    def exists(self, workflow_id: str) -> bool:
        try:
            return self._store.exists(workflow_id)
        except ObjectStoreError as exc:
            raise ContentPlanPersistenceError(f"failed to check existence: {exc}") from exc

    def load(self, workflow_id: str) -> LoadedContentPlan:
        try:
            data, etag = self._store.read(workflow_id)
        except ObjectNotFoundError as exc:
            raise ContentPlanNotFoundError(f"workflow_id={workflow_id} not found") from exc
        except ObjectDeserializationError as exc:
            raise ContentPlanDeserializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ContentPlanPersistenceError(f"failed to read workflow_id={workflow_id}: {exc}") from exc

        document = ContentPlanDocument.from_dict(data)
        return LoadedContentPlan(document=document, etag=etag)

    def save(self, document: ContentPlanDocument, *, expected_etag: str | None = None) -> str:
        """Persist `document`. Pass expected_etag=None only to create a new
        plan (fails if one already exists); pass the ETag from a prior
        load() to update it (fails if it changed since)."""

        data = document.to_dict()
        try:
            return self._store.write(document.workflow_id, data, expected_etag=expected_etag)
        except ObjectConcurrentModificationError as exc:
            raise ContentPlanConcurrentModificationError(
                f"workflow_id={document.workflow_id} was modified concurrently"
            ) from exc
        except ObjectSerializationError as exc:
            raise ContentPlanSerializationError(str(exc)) from exc
        except ObjectStoreError as exc:
            raise ContentPlanPersistenceError(f"failed to write workflow_id={document.workflow_id}: {exc}") from exc
