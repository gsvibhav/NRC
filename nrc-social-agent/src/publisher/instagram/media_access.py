"""The media-access boundary: converts a durable S3 reference
(`MediaAssetReference.storage_reference`, `{"bucket", "key"}`) into a
temporary, externally-reachable URL Meta can fetch — without making the
bucket public, without embedding AWS credentials in anything Meta sees,
and without ever persisting the URL anywhere.

**Chosen mechanism: S3 presigned GET URLs** (`boto3`'s
`generate_presigned_url`) — the standard, narrowly-scoped way to grant
time-limited, unauthenticated read access to exactly one S3 object
without altering the bucket's own access policy. The URL embeds a
signature valid only for this one object until it expires; it is not an
AWS credential and grants no other access.

**Validity window**: Meta's own Content Publishing documentation states
only that a media *container* expires 24 hours after creation if never
published — it does not specify how long the source media URL itself
must remain reachable during processing. `INSTAGRAM_MEDIA_URL_TTL_SECONDS`
(config.py) is deliberately conservative (default: 30 minutes) to safely
cover container creation *and* the bounded status-polling window that
follows it, well short of the 24-hour container-expiry ceiling. The
presigned URL is generated fresh for every dispatch attempt (never
reused across attempts, never persisted) — see dispatch_service.py's
`MEDIA_ACCESS_PREPARED` step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from ...execution.errors import ExecutionPermanentError

logger = logging.getLogger(__name__)


class MediaAccessError(ExecutionPermanentError):
    """The durable S3 reference could not be converted into a temporary
    externally-reachable source (e.g. the object no longer exists) —
    permanent, since retrying without a real fix would fail identically."""

    user_message = "The approved media could not be prepared for publishing."


@dataclass(frozen=True)
class TemporaryMediaSource:
    url: str
    expires_at: str
    media_type: str


class PublicationMediaAccess(Protocol):
    def create_temporary_source(self, *, bucket: str, key: str, media_type: str, ttl_seconds: int) -> TemporaryMediaSource: ...


class S3PresignedMediaAccess:
    """The real implementation — constructed only in main.py. Never logs
    the full generated URL (only the asset's bucket/key and the TTL),
    never persists it, and never widens the bucket's own access policy."""

    def __init__(self, client) -> None:
        self._client = client

    def create_temporary_source(self, *, bucket: str, key: str, media_type: str, ttl_seconds: int) -> TemporaryMediaSource:
        from datetime import datetime, timedelta, timezone

        try:
            url = self._client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_seconds,
            )
        except Exception as exc:
            raise MediaAccessError(f"failed to create a temporary media source for bucket={bucket} key={key}: {exc}") from exc

        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        logger.info(
            "Temporary media source created bucket=%s key=%s ttl_seconds=%s (URL not logged)",
            bucket, key, ttl_seconds,
        )
        return TemporaryMediaSource(url=url, expires_at=expires_at, media_type=media_type)
