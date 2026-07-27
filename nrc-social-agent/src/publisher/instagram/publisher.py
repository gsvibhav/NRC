"""`InstagramPublisher`: the Instagram-specific implementation of the
generic `Publisher.advance()` contract (../protocol.py).

**Verified Instagram Content Publishing flow** (see the completion
report for exact sources): create a media container (`POST
/<ig-id>/media`) → poll its `status_code` until `FINISHED` → publish it
(`POST /<ig-id>/media_publish?creation_id=...`) → the response's `id` is
the new, durably-distinct external media ID (never the same as the
container ID).

**One `advance()` call performs exactly one durable step**, dispatched
on `execution.checkpoint` — never a blocking multi-step loop. Bounded
polling/backoff across *multiple* `advance()` calls is
`dispatch_service.py`'s job, not this adapter's; this keeps `advance()`
trivially unit-testable with a fake HTTP client and no sleeping at all.

**The one asymmetric design choice this file makes** (documented in
detail in README.md's "Ambiguous outcome recovery"): an ambiguous/timed-
out *container creation* call is treated as ordinarily retryable — a
retry simply creates a fresh container, and an orphaned unpublished
container is harmless (it silently expires in 24 hours; nothing was ever
posted). An ambiguous *publish* call is never retried blindly — see the
`CONTAINER_READY`/`PUBLISH_REQUESTED` handlers below and
`dispatch_service.py`'s own docstring for the full reconciliation design.
Duplicating a container costs nothing visible; duplicating a publish
creates a second, publicly-visible Instagram post — the two are not
symmetric risks, so they are not treated identically.
"""

from __future__ import annotations

from ...execution.models import DispatchCheckpoint, ExecutionDocument
from ...publication.models import PublicationContent, PublicationPackage
from ..errors import PublisherAmbiguousError, PublisherPermanentError, PublisherRetryableError
from ..models import FailureCategory, PublisherFailure, PublisherResult, PublisherStepResult
from .caption import assemble_instagram_caption
from .client import MetaHttpClient, MetaHttpError
from .error_mapping import map_meta_http_error
from .media_access import PublicationMediaAccess
from .models import InstagramMediaMode, resolve_instagram_media_mode
from .validation import validate_assembled_caption, validate_credentials, validate_single_media_reference

_STATUS_FINISHED = "FINISHED"
_STATUS_IN_PROGRESS = "IN_PROGRESS"
_STATUS_ERROR = "ERROR"
_STATUS_EXPIRED = "EXPIRED"
_STATUS_PUBLISHED = "PUBLISHED"


class InstagramPublisher:
    def __init__(
        self,
        *,
        http_client: MetaHttpClient,
        media_access: PublicationMediaAccess,
        clock,
        instagram_account_id: str,
        access_token: str,
        api_version: str,
        media_url_ttl_seconds: int,
        request_timeout_seconds: float,
    ) -> None:
        self._http_client = http_client
        self._media_access = media_access
        self._clock = clock
        self._instagram_account_id = instagram_account_id
        self._access_token = access_token
        self._api_version = api_version
        self._media_url_ttl_seconds = media_url_ttl_seconds
        self._request_timeout_seconds = request_timeout_seconds

    def advance(self, execution: ExecutionDocument, publication: PublicationPackage) -> PublisherStepResult:
        checkpoint = execution.checkpoint
        if checkpoint is DispatchCheckpoint.NOT_STARTED:
            return self._create_container(execution, publication)
        if checkpoint in (DispatchCheckpoint.CONTAINER_CREATED, DispatchCheckpoint.CONTAINER_PROCESSING):
            return self._check_container_status(execution)
        if checkpoint is DispatchCheckpoint.CONTAINER_READY:
            return self._publish(execution, publication)
        if checkpoint is DispatchCheckpoint.PUBLISH_REQUESTED:
            return self._reconcile_ambiguous_publish(execution)
        raise PublisherPermanentError(f"no advance() step defined for checkpoint={checkpoint!r}")

    # --- NOT_STARTED -> CONTAINER_CREATED -----------------------------------

    def _create_container(self, execution: ExecutionDocument, publication: PublicationPackage) -> PublisherStepResult:
        validate_credentials(instagram_account_id=self._instagram_account_id, access_token=self._access_token)

        content = publication.content if isinstance(publication.content, PublicationContent) else PublicationContent.from_dict(publication.content or {})
        caption = assemble_instagram_caption(content)
        validate_assembled_caption(caption)

        media = validate_single_media_reference(publication)
        # Milestone 11A: consume the already-resolved, persisted
        # placement — never re-infer it from the raw media type here.
        media_mode = resolve_instagram_media_mode(publication.resolved_placement().placement)

        source = self._media_access.create_temporary_source(
            bucket=media.storage_reference["bucket"], key=media.storage_reference["key"],
            media_type=media.media_type, ttl_seconds=self._media_url_ttl_seconds,
        )

        params = {"caption": caption}
        if media_mode is InstagramMediaMode.REELS:
            params["media_type"] = "REELS"
            params["video_url"] = source.url
        else:
            params["image_url"] = source.url

        try:
            response = self._http_client.create_media_container(
                instagram_account_id=self._instagram_account_id, params=params, access_token=self._access_token,
                api_version=self._api_version, timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError as exc:
            # Ambiguous container-creation is treated as retryable, not as
            # a duplicate-publication risk — see this module's docstring.
            raise map_meta_http_error(exc, operation="create_media_container", is_irreversible=False) from exc

        container_id = response.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise PublisherPermanentError("create_media_container response had no usable container id")

        return PublisherStepResult(
            next_checkpoint=DispatchCheckpoint.CONTAINER_CREATED,
            platform_state_update={"container_id": container_id, "last_checked_at": self._clock.now_iso()},
        )

    # --- CONTAINER_CREATED / CONTAINER_PROCESSING ---------------------------

    def _check_container_status(self, execution: ExecutionDocument) -> PublisherStepResult:
        container_id = (execution.platform_state or {}).get("container_id")
        if not container_id:
            raise PublisherPermanentError("no container_id recorded for a CONTAINER_CREATED/CONTAINER_PROCESSING execution")

        try:
            response = self._http_client.get_container_status(
                container_id=container_id, access_token=self._access_token, api_version=self._api_version,
                timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError as exc:
            raise map_meta_http_error(exc, operation="get_container_status", is_irreversible=False) from exc

        status_code = response.get("status_code")
        now = self._clock.now_iso()

        if status_code == _STATUS_FINISHED:
            return PublisherStepResult(
                next_checkpoint=DispatchCheckpoint.CONTAINER_READY,
                platform_state_update={"last_status": status_code, "last_checked_at": now},
            )
        if status_code == _STATUS_IN_PROGRESS:
            return PublisherStepResult(
                next_checkpoint=DispatchCheckpoint.CONTAINER_PROCESSING,
                platform_state_update={"last_status": status_code, "last_checked_at": now},
            )
        if status_code == _STATUS_ERROR:
            raise PublisherPermanentError(
                f"container {container_id} failed to process (status_code=ERROR)",
                reset_checkpoint=DispatchCheckpoint.NOT_STARTED,
            )
        if status_code == _STATUS_EXPIRED:
            raise PublisherPermanentError(
                f"container {container_id} expired before it could be published (status_code=EXPIRED)",
                reset_checkpoint=DispatchCheckpoint.NOT_STARTED,
            )

        raise PublisherRetryableError(f"unrecognized container status_code={status_code!r}")

    # --- CONTAINER_READY -> (dispatch service pre-persists PUBLISH_REQUESTED, then) publish --

    def _publish(self, execution: ExecutionDocument, publication: PublicationPackage) -> PublisherStepResult:
        container_id = (execution.platform_state or {}).get("container_id")
        if not container_id:
            raise PublisherPermanentError("no container_id recorded for a CONTAINER_READY execution")

        try:
            response = self._http_client.publish_media(
                instagram_account_id=self._instagram_account_id, container_id=container_id,
                access_token=self._access_token, api_version=self._api_version,
                timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError as exc:
            # The one genuinely irreversible call — ambiguity here must
            # never be retried blindly (see this module's docstring and
            # dispatch_service.py).
            raise map_meta_http_error(
                exc, operation="publish_media", is_irreversible=True,
                partial_platform_state={"container_id": container_id},
            ) from exc

        external_media_id = response.get("id")
        if not isinstance(external_media_id, str) or not external_media_id:
            raise PublisherAmbiguousError(
                "publish_media returned a successful-looking response with no usable media id",
                partial_platform_state={"container_id": container_id},
            )

        result = PublisherResult(
            platform="instagram", external_media_id=external_media_id, external_container_id=container_id,
            permalink=None, published_at=self._clock.now_iso(), verified_at=self._clock.now_iso(),
            media_type=resolve_instagram_media_mode(publication.resolved_placement().placement).value,
        )
        return PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=result)

    # --- PUBLISH_REQUESTED: reconciliation only, never re-calls publish_media() ---

    def _reconcile_ambiguous_publish(self, execution: ExecutionDocument) -> PublisherStepResult:
        container_id = (execution.platform_state or {}).get("container_id")
        if not container_id:
            raise PublisherPermanentError("no container_id recorded for a PUBLISH_REQUESTED execution")

        try:
            response = self._http_client.get_container_status(
                container_id=container_id, access_token=self._access_token, api_version=self._api_version,
                timeout_seconds=self._request_timeout_seconds,
            )
        except MetaHttpError as exc:
            # Even a failure to reconcile must not be treated as license to
            # republish — surfaced as ambiguous again, never retryable.
            raise PublisherAmbiguousError(
                f"could not reconcile ambiguous publish outcome: {exc}",
                partial_platform_state={"container_id": container_id},
            ) from exc

        status_code = response.get("status_code")
        now = self._clock.now_iso()

        if status_code == _STATUS_PUBLISHED:
            # Reconciled evidence of success — the external_media_id
            # cannot be recovered this way (Meta does not expose a
            # container -> media reverse lookup), a documented,
            # accepted limitation. Never republish.
            result = PublisherResult(
                platform="instagram", external_media_id=None, external_container_id=container_id,
                permalink=None, published_at=now, verified_at=now, media_type="UNKNOWN",
            )
            return PublisherStepResult(next_checkpoint=DispatchCheckpoint.VERIFIED, result=result)

        if status_code in (_STATUS_ERROR, _STATUS_EXPIRED):
            # Definitively did not publish — safe to report a permanent
            # failure; a genuinely fresh attempt (new container) is safe
            # later, since nothing was ever posted.
            raise PublisherPermanentError(
                f"container {container_id} did not publish (status_code={status_code}) after an ambiguous attempt",
                reset_checkpoint=DispatchCheckpoint.NOT_STARTED,
            )

        # Still FINISHED or IN_PROGRESS: genuinely still ambiguous. Never
        # republish — surface for conservative operator intervention.
        failure = PublisherFailure(
            category=FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME, retryable=False, operation="publish_media",
            safe_message="Instagram publication outcome could not be confirmed and requires manual review.",
            occurred_at=now,
        )
        raise PublisherPermanentError(
            f"publish outcome for container {container_id} remains ambiguous (status_code={status_code})",
            failure=failure,
        )
