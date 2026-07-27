"""Domain models for the Publication Preparation Layer (Milestone 8): the
immutable, minimal, platform-facing publication package a future publisher
adapter consumes — and nothing else. See builder.py/service.py for how one
is produced from an approved draft version.

`schema_version` (the content-schema version, config-supplied — mirrors
`DraftDocument.schema_version`/`ContentPlanDocument.schema_version`) is
distinct from `version` (the persisted document *envelope* version,
`CURRENT_DOCUMENT_VERSION` below — mirrors every other domain's identical
`version` field). Both start at `1`; they change independently, exactly
like every other pipeline stage's two version numbers.

**Schema v2 (Milestone 11A)** adds `placement` — an explicit, persisted
`{channel, placement, media_mode}` record of where on the channel this
package will actually appear (`feed` vs `reel`), resolved once from the
approved media's MIME type at build time (see `resolve_placement()`) and
never re-inferred by a consumer afterward (see
`PublicationPackage.resolved_placement()`). This exists because
`output_type` alone (e.g. `instagram_reel_caption`) does not reliably
describe the actual platform placement — see the Milestone 11A planning
report and README.md's "Explicit placement contract" section. The field
is optional/additive: a v1 package (created before it existed) still
loads exactly as its original bytes; `resolved_placement()` derives the
same answer in-memory for such a package, without ever writing back to
storage — package immutability is fully preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    InvalidPublicationStatusError,
    PublicationDeserializationError,
    PublicationVersionMismatchError,
    UnsupportedPublicationMediaTypeError,
    UnsupportedPublicationOutputTypeError,
)

CURRENT_DOCUMENT_VERSION = 2
SUPPORTED_DOCUMENT_VERSIONS = {1, 2}


class PublicationStatus(str, Enum):
    """Deliberately two-valued, mirroring AnalysisStatus/ContentPlanStatus/
    DraftStatus's identical precedent (see their docstrings): this service
    either completes a preparation attempt (READY_FOR_PUBLISHING) or
    reports a controlled failure (FAILED), never a persisted long-running
    in-between state. Deliberately does NOT include PUBLISHING/PUBLISHED/
    SCHEDULED/PLATFORM_REJECTED/PARTIALLY_PUBLISHED — those describe a
    future, separate "publication execution" record this milestone does
    not implement (see README.md's "Immutability" section)."""

    READY_FOR_PUBLISHING = "READY_FOR_PUBLISHING"
    FAILED = "FAILED"


class PublicationChannel(str, Enum):
    """The only channel this milestone can prepare for — see
    _CHANNEL_BY_OUTPUT_TYPE below for why this isn't derived from the
    planning registry's own `channel` display string."""

    INSTAGRAM = "instagram"


# Deliberately a SEPARATE, narrower mapping from content_plan_models.py's
# OUTPUT_TYPE_REGISTRY, even though today they would produce the same
# single entry. "Registered for planning", "supports generation", and
# "supports publication preparation" are three independent, deliberately
# narrowing gates — a future milestone enabling generation for a second
# output type must NOT automatically make it publication-ready; that
# requires its own explicit addition here.
_CHANNEL_BY_OUTPUT_TYPE = {
    "instagram_reel_caption": PublicationChannel.INSTAGRAM,
}


def resolve_channel(output_type: str) -> PublicationChannel:
    """Deterministic, code-owned mapping — never asks Claude, never
    infers from content text, never lets a caller pass an arbitrary
    channel string. Raises UnsupportedPublicationOutputTypeError for any
    output type not explicitly enabled here; never silently falls back to
    Instagram."""

    channel = _CHANNEL_BY_OUTPUT_TYPE.get(output_type)
    if channel is None:
        raise UnsupportedPublicationOutputTypeError(
            f"output_type={output_type!r} is not publication-preparation-supported"
        )
    return channel


class Placement(str, Enum):
    """Where on the channel this package will appear — deliberately
    independent of `output_type` (see Milestone 11A's authoring-contract
    report: `output_type=instagram_reel_caption` does not, on its own,
    mean the content will actually appear as a Reel). Resolved once, here,
    from the approved media's own MIME type — never re-inferred later by
    a publisher adapter."""

    FEED = "feed"
    REEL = "reel"


class MediaMode(str, Enum):
    SINGLE_IMAGE = "single_image"
    VIDEO = "video"


# Deterministic, code-owned — the one place a media MIME type becomes a
# platform placement. A publisher adapter (src/publisher/instagram/)
# consumes the result via `PublicationPackage.resolved_placement()`; it
# never re-derives this from the raw media type itself.
_PLACEMENT_BY_MEDIA_TYPE = {
    "image/jpeg": (Placement.FEED, MediaMode.SINGLE_IMAGE),
    "video/mp4": (Placement.REEL, MediaMode.VIDEO),
}


@dataclass(frozen=True)
class PublicationPlacement:
    """Persisted once, at package-build time, and never recomputed by a
    consumer — see `resolve_placement()` and
    `PublicationPackage.resolved_placement()`."""

    channel: PublicationChannel
    placement: Placement
    media_mode: MediaMode

    def to_dict(self) -> dict:
        return {"channel": self.channel.value, "placement": self.placement.value, "media_mode": self.media_mode.value}

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationPlacement":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected an object for 'placement', got {data!r}")

        try:
            channel = PublicationChannel(data.get("channel"))
        except ValueError as exc:
            raise PublicationDeserializationError(f"unrecognized field: 'placement.channel' (got {data.get('channel')!r})") from exc
        try:
            placement = Placement(data.get("placement"))
        except ValueError as exc:
            raise PublicationDeserializationError(f"unrecognized field: 'placement.placement' (got {data.get('placement')!r})") from exc
        try:
            media_mode = MediaMode(data.get("media_mode"))
        except ValueError as exc:
            raise PublicationDeserializationError(f"unrecognized field: 'placement.media_mode' (got {data.get('media_mode')!r})") from exc

        return cls(channel=channel, placement=placement, media_mode=media_mode)


def resolve_placement(channel: PublicationChannel, media_type: str) -> PublicationPlacement:
    """Deterministic, code-owned mapping from the approved media's MIME
    type to a platform placement — never asks Claude, never infers from
    content text, never derived from `output_type`. Raises
    UnsupportedPublicationMediaTypeError for any MIME type not explicitly
    enabled here; never silently defaults to one placement over another."""

    entry = _PLACEMENT_BY_MEDIA_TYPE.get(media_type)
    if entry is None:
        raise UnsupportedPublicationMediaTypeError(f"media_type={media_type!r} has no known publication placement")
    placement, media_mode = entry
    return PublicationPlacement(channel=channel, placement=placement, media_mode=media_mode)


@dataclass(frozen=True)
class PublicationContent:
    """Structurally identical to the approved draft's own content shape
    (see draft_models.InstagramReelCaptionContent) — deliberately a
    separate dataclass, not a reused import, so the publication domain's
    contract can never silently drift just because the authoring domain's
    shape changes; see builder.py for the (mechanical-only) mapping
    between the two."""

    caption: str
    hashtags: list = field(default_factory=list)
    cta: str | None = None

    def to_dict(self) -> dict:
        return {"caption": self.caption, "hashtags": self.hashtags, "cta": self.cta}

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationContent":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected an object for 'content', got {data!r}")

        caption = data.get("caption")
        if not isinstance(caption, str) or not caption:
            raise PublicationDeserializationError(f"invalid field: 'content.caption' (got {caption!r})")

        hashtags = data.get("hashtags", [])
        if not isinstance(hashtags, list) or not all(isinstance(h, str) for h in hashtags):
            raise PublicationDeserializationError(f"invalid field: 'content.hashtags' (got {hashtags!r})")

        cta = data.get("cta")
        if cta is not None and not isinstance(cta, str):
            raise PublicationDeserializationError(f"invalid field: 'content.cta' (got {cta!r})")

        return cls(caption=caption, hashtags=hashtags, cta=cta or None)


@dataclass(frozen=True)
class MediaAssetReference:
    """A durable reference only — never bytes, never a presigned/temporary
    URL, never credentials. `storage_reference` is a plain
    `{"bucket": ..., "key": ...}` dict, deliberately not a richer object,
    so a future publisher can request temporary access through its own
    infrastructure boundary without this package needing to know how."""

    asset_id: str
    media_type: str
    storage_reference: dict

    def to_dict(self) -> dict:
        return {"asset_id": self.asset_id, "media_type": self.media_type, "storage_reference": self.storage_reference}

    @classmethod
    def from_dict(cls, data: dict) -> "MediaAssetReference":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected an object for a media entry, got {data!r}")

        asset_id = data.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise PublicationDeserializationError(f"invalid field: 'media[].asset_id' (got {asset_id!r})")

        media_type = data.get("media_type")
        if not isinstance(media_type, str) or not media_type:
            raise PublicationDeserializationError(f"invalid field: 'media[].media_type' (got {media_type!r})")

        storage_reference = data.get("storage_reference")
        if not isinstance(storage_reference, dict) or "bucket" not in storage_reference or "key" not in storage_reference:
            raise PublicationDeserializationError(
                f"invalid field: 'media[].storage_reference' (got {storage_reference!r})"
            )

        return cls(asset_id=asset_id, media_type=media_type, storage_reference=storage_reference)


@dataclass(frozen=True)
class PublicationDraftReference:
    draft_id: str
    version_number: int

    def to_dict(self) -> dict:
        return {"draft_id": self.draft_id, "version_number": self.version_number}

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationDraftReference":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected an object for 'draft', got {data!r}")

        draft_id = data.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            raise PublicationDeserializationError(f"invalid field: 'draft.draft_id' (got {draft_id!r})")

        version_number = data.get("version_number")
        if not isinstance(version_number, int) or isinstance(version_number, bool) or version_number < 1:
            raise PublicationDeserializationError(f"invalid field: 'draft.version_number' (got {version_number!r})")

        return cls(draft_id=draft_id, version_number=version_number)


@dataclass(frozen=True)
class PublicationApproval:
    """Only the minimum identity required for auditing who approved this
    exact content — never the full Telegram user profile, never
    conversation content."""

    approved_at: str
    approved_by_telegram_user_id: int

    def to_dict(self) -> dict:
        return {"approved_at": self.approved_at, "approved_by_telegram_user_id": self.approved_by_telegram_user_id}

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationApproval":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected an object for 'approval', got {data!r}")

        approved_at = data.get("approved_at")
        if not isinstance(approved_at, str) or not approved_at:
            raise PublicationDeserializationError(f"invalid field: 'approval.approved_at' (got {approved_at!r})")

        approved_by = data.get("approved_by_telegram_user_id")
        if not isinstance(approved_by, int) or isinstance(approved_by, bool):
            raise PublicationDeserializationError(
                f"invalid field: 'approval.approved_by_telegram_user_id' (got {approved_by!r})"
            )

        return cls(approved_at=approved_at, approved_by_telegram_user_id=approved_by)


_REQUIRED_STRING_FIELDS = (
    "publication_id", "workflow_id", "plan_id", "output_id", "output_type",
    "created_at", "updated_at",
)


@dataclass(frozen=True)
class PublicationPackage:
    """The persisted envelope, at
    publications/<workflow_id>/<output_id>.json (see repository.py). One
    package represents exactly one approved workflow + one approved draft
    + one draft version + one output + one target channel — never
    multiple outputs, channels, or draft versions, and never unapproved
    content (see builder.py/service.py for how this is enforced, not just
    documented)."""

    publication_id: str
    workflow_id: str
    plan_id: str
    output_id: str
    output_type: str
    channel: PublicationChannel
    status: PublicationStatus
    schema_version: int
    created_at: str
    updated_at: str
    draft: dict | None = None
    content: dict | None = None
    media: list = field(default_factory=list)
    approval: dict | None = None
    source_versions: dict | None = None
    placement: dict | None = None
    metadata: dict = field(default_factory=dict)
    version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "publication_id": self.publication_id,
            "workflow_id": self.workflow_id,
            "plan_id": self.plan_id,
            "output_id": self.output_id,
            "output_type": self.output_type,
            "channel": self.channel.value,
            "status": self.status.value,
            "schema_version": self.schema_version,
            "draft": self.draft,
            "content": self.content,
            "media": self.media,
            "approval": self.approval,
            "source_versions": self.source_versions,
            "placement": self.placement,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    def resolved_placement(self) -> PublicationPlacement:
        """The one place any consumer (a publisher adapter, a preview
        renderer, a future analytics reader) gets this package's
        placement — never re-inferred by the caller itself. Returns the
        persisted `placement` (every package created since schema v2)
        or, for a v1 package created before this field existed, derives
        it in-memory from the package's own already-persisted `channel`
        and `media[0]['media_type']` — *never written back to storage*,
        preserving immutability exactly: a v1 package remains, on disk,
        exactly the bytes it always was; only the in-memory object this
        method returns differs."""

        if self.placement is not None:
            return PublicationPlacement.from_dict(self.placement)
        if not self.media:
            raise UnsupportedPublicationMediaTypeError(
                f"publication_id={self.publication_id} has no media to derive a placement from"
            )
        media_type = self.media[0].get("media_type") if isinstance(self.media[0], dict) else self.media[0].media_type
        return resolve_placement(self.channel, media_type)

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationPackage":
        if not isinstance(data, dict):
            raise PublicationDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise PublicationVersionMismatchError(
                f"unsupported publication document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        for name in _REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise PublicationDeserializationError(f"missing or invalid field: {name!r}")

        channel_raw = data.get("channel")
        try:
            channel = PublicationChannel(channel_raw)
        except ValueError as exc:
            raise PublicationDeserializationError(f"unrecognized channel: {channel_raw!r}") from exc

        status_raw = data.get("status")
        try:
            status = PublicationStatus(status_raw)
        except ValueError as exc:
            raise InvalidPublicationStatusError(f"unrecognized status: {status_raw!r}") from exc

        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise PublicationDeserializationError(f"invalid field: 'schema_version' (got {schema_version!r})")

        draft = data.get("draft")
        if draft is not None and not isinstance(draft, dict):
            raise PublicationDeserializationError(f"invalid field: 'draft' (got {draft!r})")

        content = data.get("content")
        if content is not None and not isinstance(content, dict):
            raise PublicationDeserializationError(f"invalid field: 'content' (got {content!r})")

        media = data.get("media")
        if not isinstance(media, list):
            raise PublicationDeserializationError(f"invalid field: 'media' (got {media!r})")

        approval = data.get("approval")
        if approval is not None and not isinstance(approval, dict):
            raise PublicationDeserializationError(f"invalid field: 'approval' (got {approval!r})")

        source_versions = data.get("source_versions")
        if source_versions is not None and not isinstance(source_versions, dict):
            raise PublicationDeserializationError(f"invalid field: 'source_versions' (got {source_versions!r})")

        placement = data.get("placement")
        if placement is not None and not isinstance(placement, dict):
            raise PublicationDeserializationError(f"invalid field: 'placement' (got {placement!r})")

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise PublicationDeserializationError(f"invalid field: 'metadata' (got {metadata!r})")

        return cls(
            publication_id=data["publication_id"],
            workflow_id=data["workflow_id"],
            plan_id=data["plan_id"],
            output_id=data["output_id"],
            output_type=data["output_type"],
            channel=channel,
            status=status,
            schema_version=schema_version,
            draft=draft,
            content=content,
            media=media,
            approval=approval,
            source_versions=source_versions,
            placement=placement,
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=metadata,
            version=version,
        )
