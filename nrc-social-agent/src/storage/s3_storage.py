"""S3 storage adapter — the sole persistence mechanism for uploaded media.

Per ARCHITECTURE.md and DECISIONS.md #3, there is no database; this adapter
is deliberately minimal (write-only, single-object PutObject calls) since
Version 1's persistence needs don't require anything more.
"""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# S3's conditional-write precondition failure is reported under one of these
# codes depending on API version/region.
_COLLISION_ERROR_CODES = {"PreconditionFailed", "ConditionalRequestConflict"}


class S3UploadError(Exception):
    """Raised when an upload to S3 fails for any reason."""


class S3ObjectCollisionError(S3UploadError):
    """Raised when an object already exists at the target key.

    Should be effectively unreachable in practice: object keys embed a
    freshly generated workflow ID (see src/media/ingestion.py), so a
    collision would require a UUID4 collision or a caller bug — this
    exists as defense in depth, not as the primary collision-avoidance
    mechanism.
    """


class S3DownloadError(Exception):
    """Raised when a download from S3 fails for any reason."""


class S3Storage:
    """Thin wrapper around a boto3 S3 client, scoped to one bucket/prefix."""

    def __init__(self, client, bucket_name: str, key_prefix: str) -> None:
        self._client = client
        self._bucket_name = bucket_name
        self._key_prefix = key_prefix

    def build_object_key(self, telegram_user_id: int, workflow_id: str, filename: str) -> str:
        return f"{self._key_prefix}/{telegram_user_id}/{workflow_id}/original/{filename}"

    def upload_file(
        self,
        local_path: str,
        key: str,
        *,
        content_type: str,
        metadata: dict[str, str],
    ) -> None:
        """Upload the file at `local_path` to `key`. Streams from disk — the
        file is opened and read in chunks by the underlying HTTP client, not
        loaded into memory as a single object.

        Raises S3ObjectCollisionError if an object already exists at `key`,
        or S3UploadError for any other upload failure.
        """

        try:
            with open(local_path, "rb") as body:
                self._client.put_object(
                    Bucket=self._bucket_name,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    Metadata=metadata,
                    # Conditional write: never overwrite an existing object.
                    IfNoneMatch="*",
                )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in _COLLISION_ERROR_CODES:
                raise S3ObjectCollisionError(f"object already exists at key={key}") from exc
            raise S3UploadError(f"failed to upload key={key}: {error_code or exc}") from exc
        except OSError as exc:
            raise S3UploadError(f"failed to read local file for upload: {exc}") from exc

    def download_file(self, key: str, local_path: str) -> None:
        """Download the object at `key` to `local_path`. Streams the
        response body in bounded chunks — never loads the full object into
        memory as a single Python value.

        Raises S3DownloadError for any read or write failure.
        """

        try:
            response = self._client.get_object(Bucket=self._bucket_name, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            raise S3DownloadError(f"failed to download key={key}: {error_code or exc}") from exc

        try:
            with open(local_path, "wb") as body:
                for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                    body.write(chunk)
        except OSError as exc:
            raise S3DownloadError(f"failed to write local file for download: {exc}") from exc
