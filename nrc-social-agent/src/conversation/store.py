"""S3-backed store for conversation documents, fixed to the `conversation/`
prefix — a frozen, non-configurable prefix, same as workflow's `state/`.
"""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

CONVERSATION_KEY_PREFIX = "conversation"


class ConversationStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=CONVERSATION_KEY_PREFIX)
