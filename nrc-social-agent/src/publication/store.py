"""S3-backed store for publication packages, fixed to the `publications/`
prefix — a new, dedicated namespace (never under the workflow document,
never under the draft-version path)."""

from __future__ import annotations

from ..storage.json_object_store import JsonObjectStore

PUBLICATION_KEY_PREFIX = "publications"


class PublicationStore(JsonObjectStore):
    def __init__(self, client, bucket_name: str) -> None:
        super().__init__(client=client, bucket_name=bucket_name, key_prefix=PUBLICATION_KEY_PREFIX)
