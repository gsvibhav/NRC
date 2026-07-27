"""S3-backed store for execution records, fixed to the `executions/`
prefix — a new, dedicated namespace (never under the workflow document,
never under the publication path)."""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

EXECUTION_KEY_PREFIX = "executions"


class ExecutionStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=EXECUTION_KEY_PREFIX)
