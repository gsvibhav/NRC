"""S3-backed store for analysis documents, fixed to the `analysis/` prefix.

A frozen, non-configurable prefix, same as workflow's `state/` and
conversation's `conversation/`. Reuses the generic JsonObjectStore
primitive from src/storage/ — no new S3 mechanics introduced.
"""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

ANALYSIS_KEY_PREFIX = "analysis"


class AnalysisStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=ANALYSIS_KEY_PREFIX)
