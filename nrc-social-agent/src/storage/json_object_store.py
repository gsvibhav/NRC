"""Generic S3-backed JSON document store.

A thin, prefix-scoped CRUD primitive over S3 PutObject/GetObject/HeadObject,
dealing only in plain dicts and S3 mechanics (keys, ETags, conditional
writes). It has no notion of "workflow" or any other domain concept — that
belongs one layer up (see src/workflow/repository.py), which is what keeps
business logic from ever touching raw S3 details.
"""

from __future__ import annotations

import json
import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Conditional-write precondition failure, reported under one of these codes
# depending on API version/region (same set used by src/storage/s3_storage.py).
_PRECONDITION_FAILED_CODES = {"PreconditionFailed", "ConditionalRequestConflict"}
_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


class ObjectStoreError(Exception):
    """Raised when an S3 read/write fails for any reason."""


class ObjectNotFoundError(ObjectStoreError):
    """Raised when no object exists at the requested key."""


class ObjectConcurrentModificationError(ObjectStoreError):
    """Raised when a conditional write's precondition fails.

    Means either: the object already existed when the caller expected to
    create a new one (`expected_etag=None`), or the object was modified by
    someone else since the caller last read it (`expected_etag=<stale>`).
    """


class ObjectSerializationError(ObjectStoreError):
    """Raised when `data` can't be encoded as JSON."""


class ObjectDeserializationError(ObjectStoreError):
    """Raised when a stored object's bytes aren't valid JSON."""


class JsonObjectStore:
    """CRUD for one JSON document per key, under a fixed bucket/prefix."""

    def __init__(self, client, bucket_name: str, key_prefix: str) -> None:
        self._client = client
        self._bucket_name = bucket_name
        self._key_prefix = key_prefix

    def _build_key(self, document_id: str) -> str:
        return f"{self._key_prefix}/{document_id}.json"

    def read(self, document_id: str) -> tuple[dict, str]:
        """Return (data, etag) for `document_id`.

        Raises ObjectNotFoundError if it doesn't exist, ObjectStoreError for
        any other read failure, or ObjectDeserializationError if the stored
        bytes aren't valid JSON.
        """

        key = self._build_key(document_id)
        try:
            response = self._client.get_object(Bucket=self._bucket_name, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(f"no object at key={key}") from exc
            raise ObjectStoreError(f"failed to read key={key}: {error_code or exc}") from exc

        body = response["Body"].read()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ObjectDeserializationError(f"invalid JSON at key={key}: {exc}") from exc

        return data, response["ETag"]

    def write(self, document_id: str, data: dict, *, expected_etag: str | None = None) -> str:
        """Write `data` to `document_id` and return the new ETag.

        If `expected_etag` is None, the write only succeeds if no object
        currently exists at this key (conditional create). Otherwise, it
        only succeeds if the object's current ETag matches `expected_etag`
        (optimistic-concurrency update) — see src/workflow/manager.py for
        why this matters.

        Raises ObjectConcurrentModificationError if the precondition fails,
        ObjectSerializationError if `data` isn't JSON-serializable, or
        ObjectStoreError for any other write failure.
        """

        key = self._build_key(document_id)
        try:
            body = json.dumps(data).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ObjectSerializationError(f"failed to serialize document for key={key}: {exc}") from exc

        put_kwargs = dict(
            Bucket=self._bucket_name,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        if expected_etag is None:
            put_kwargs["IfNoneMatch"] = "*"
        else:
            put_kwargs["IfMatch"] = expected_etag

        try:
            response = self._client.put_object(**put_kwargs)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _PRECONDITION_FAILED_CODES:
                raise ObjectConcurrentModificationError(f"precondition failed for key={key}") from exc
            raise ObjectStoreError(f"failed to write key={key}: {error_code or exc}") from exc

        return response["ETag"]

    def exists(self, document_id: str) -> bool:
        key = self._build_key(document_id)
        try:
            self._client.head_object(Bucket=self._bucket_name, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _NOT_FOUND_CODES:
                return False
            raise ObjectStoreError(f"failed to check existence of key={key}: {error_code or exc}") from exc
        return True
