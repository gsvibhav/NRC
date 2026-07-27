"""S3-backed store for workflow documents, fixed to the `state/` prefix.

Per this milestone's S3 layout (see README.md), `state/` is a frozen,
non-configurable prefix — unlike media's `S3_KEY_PREFIX`, it isn't meant to
vary per deployment.
"""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

STATE_KEY_PREFIX = "state"


class WorkflowStateStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=STATE_KEY_PREFIX)
