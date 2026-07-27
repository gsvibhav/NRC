"""S3-backed store for content plan documents, fixed to the `plans/` prefix.

A frozen, non-configurable prefix, same as analysis's `analysis/` and
workflow's `state/`. Reuses the generic JsonObjectStore primitive from
src/storage/ — no new S3 mechanics introduced.
"""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

CONTENT_PLAN_KEY_PREFIX = "plans"


class ContentPlanStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=CONTENT_PLAN_KEY_PREFIX)
