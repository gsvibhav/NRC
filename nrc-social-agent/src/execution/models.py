"""Domain model for the Publication Execution Layer (Milestones 9-10): a
small, mutable runtime record that tracks the act of *attempting* to
publish an already-immutable publication package — and nothing else.

**Immutable package vs. mutable execution.** `PublicationPackage`
(src/publication/models.py) is the frozen content contract, never
rewritten once `READY_FOR_PUBLISHING`. `ExecutionDocument` is a
*separate* record, keyed off the package's own `publication_id`, that
carries attempt counts, dispatch status, a durable platform-progress
checkpoint, and (once dispatch completes) a normalized platform result or
failure — none of which is ever written back onto the package.

**Schema v2 (Milestone 10)** activates the remaining `ExecutionStatus`
transitions and adds five new fields, all optional/defaulted so a v1
document (created by Milestone 9, before any dispatch was attempted)
reads back unchanged: `checkpoint` (defaults to `NOT_STARTED`), `lease`,
`platform_state`, `result`, `failure`, and `dispatch_authorization` (all
default `None`). See `DispatchCheckpoint`'s own docstring for why a
second, narrower field is needed alongside `status`.

**A single envelope-version field, not two** (unchanged from Milestone
9): every other domain in this codebase pairs a config-supplied
`schema_version` with a code-owned `version`; `ExecutionDocument` still
collapses these into one code-owned field, since there is no independent
AI-generated content schema here to track. `SUPPORTED_DOCUMENT_VERSIONS`
grew to `{1, 2}` — this is a normal, in-place schema-version bump, not a
config variable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..publication.models import PublicationChannel
from .errors import (
    ExecutionDeserializationError,
    ExecutionVersionMismatchError,
    InvalidDispatchCheckpointError,
    InvalidExecutionStatusError,
    UnsupportedExecutionChannelError,
)

CURRENT_DOCUMENT_VERSION = 2
SUPPORTED_DOCUMENT_VERSIONS = {1, 2}


class ExecutionStatus(str, Enum):
    """Declared in Milestone 9; Milestone 10 activates the three
    transitions beyond the initial `READY_FOR_DISPATCH`. This field is
    the *overall* execution lifecycle — see `DispatchCheckpoint` below
    for the finer-grained "last durable platform step" a single status
    value can't capture on its own (e.g. `DISPATCH_IN_PROGRESS` covers
    everything from "about to create a media container" through
    "container created, waiting for Meta to finish processing it")."""

    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    DISPATCH_IN_PROGRESS = "DISPATCH_IN_PROGRESS"
    DISPATCH_FAILED = "DISPATCH_FAILED"
    COMPLETED = "COMPLETED"


class DispatchCheckpoint(str, Enum):
    """The last *durable platform step* reached — orthogonal to
    `status`. A `DISPATCH_FAILED` execution's checkpoint tells recovery
    exactly how far a prior attempt got (e.g. `CONTAINER_CREATED` means a
    real Meta container already exists and must be reused, never
    recreated); a `DISPATCH_IN_PROGRESS` execution's checkpoint tells a
    losing/reloading caller the same thing. Only the minimum checkpoints
    the verified Instagram Content Publishing flow actually needs are
    declared — see src/publisher/instagram/publisher.py for exactly what
    each one gates."""

    NOT_STARTED = "NOT_STARTED"
    MEDIA_ACCESS_PREPARED = "MEDIA_ACCESS_PREPARED"
    CONTAINER_CREATED = "CONTAINER_CREATED"
    CONTAINER_PROCESSING = "CONTAINER_PROCESSING"
    CONTAINER_READY = "CONTAINER_READY"
    PUBLISH_REQUESTED = "PUBLISH_REQUESTED"
    PUBLISHED = "PUBLISHED"
    VERIFIED = "VERIFIED"


# A separate, narrow, code-owned mapping from channel -> publisher
# identifier — deliberately not just "reuse the channel string as the
# publisher name," even though today they're the same value
# ("instagram" -> "instagram"). `channel` is data-plane identity (which
# platform this content targets, decided at publication-preparation
# time); `publisher` is an *adapter-selection* identifier a future
# Publisher registry resolves to an actual implementation
# (`Publisher.publish(execution, publication_package) -> PublicationResult`,
# per this milestone's own architectural recommendation). Keeping these
# independent is exactly what lets a later milestone register a second
# publisher for the same channel (or vice versa) without touching this
# schema.
_PUBLISHER_BY_CHANNEL = {
    PublicationChannel.INSTAGRAM: "instagram",
}


def resolve_publisher(channel: PublicationChannel) -> str:
    """Deterministic, code-owned mapping — never asks Claude, never
    infers from package content, never lets a caller pass an arbitrary
    publisher string. Raises UnsupportedExecutionChannelError for any
    channel not explicitly enabled here."""

    publisher = _PUBLISHER_BY_CHANNEL.get(channel)
    if publisher is None:
        raise UnsupportedExecutionChannelError(
            f"channel={channel!r} has no known publisher mapping"
        )
    return publisher


_REQUIRED_STRING_FIELDS = ("execution_id", "publication_id", "workflow_id", "created_at", "updated_at")


@dataclass(frozen=True)
class ExecutionDocument:
    """The persisted envelope, at executions/<publication_id>.json (see
    repository.py). Deliberately holds only identity, lifecycle, and
    normalized-outcome fields — no field exists here capable of holding a
    caption, a hashtag, a prompt, a Claude response, an access token, a
    temporary media URL, or a raw platform response (see validation.py
    for the `metadata`/`platform_state`/`result`/`failure` fields'
    defensive backstops).

    `lease`, `platform_state`, `result`, `failure`, and
    `dispatch_authorization` are all plain dicts (not nested dataclasses)
    at this envelope layer — the same "generic dict at the envelope
    layer, structured dataclass one layer up" pattern already used for
    `WorkflowDocument.media`/`DraftDocument.content` — normalized/
    validated by `src/publisher/models.py`'s own dataclasses and
    `validation.py` before ever being persisted here."""

    execution_id: str
    publication_id: str
    workflow_id: str
    channel: PublicationChannel
    status: ExecutionStatus
    attempt: int
    publisher: str
    created_at: str
    updated_at: str
    checkpoint: DispatchCheckpoint = DispatchCheckpoint.NOT_STARTED
    lease: dict | None = None
    platform_state: dict | None = None
    result: dict | None = None
    failure: dict | None = None
    dispatch_authorization: dict | None = None
    metadata: dict = field(default_factory=dict)
    schema_version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "publication_id": self.publication_id,
            "workflow_id": self.workflow_id,
            "channel": self.channel.value,
            "status": self.status.value,
            "checkpoint": self.checkpoint.value,
            "attempt": self.attempt,
            "publisher": self.publisher,
            "lease": self.lease,
            "platform_state": self.platform_state,
            "result": self.result,
            "failure": self.failure,
            "dispatch_authorization": self.dispatch_authorization,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionDocument":
        if not isinstance(data, dict):
            raise ExecutionDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        schema_version = data.get("schema_version")
        if schema_version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise ExecutionVersionMismatchError(
                f"unsupported execution document schema_version: {schema_version!r} "
                f"(supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        for name in _REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise ExecutionDeserializationError(f"missing or invalid field: {name!r}")

        channel_raw = data.get("channel")
        try:
            channel = PublicationChannel(channel_raw)
        except ValueError as exc:
            raise ExecutionDeserializationError(f"unrecognized channel: {channel_raw!r}") from exc

        status_raw = data.get("status")
        try:
            status = ExecutionStatus(status_raw)
        except ValueError as exc:
            raise InvalidExecutionStatusError(f"unrecognized status: {status_raw!r}") from exc

        # A v1 document (Milestone 9) has no checkpoint at all — it is,
        # by construction, always READY_FOR_DISPATCH, i.e. NOT_STARTED.
        checkpoint_raw = data.get("checkpoint", DispatchCheckpoint.NOT_STARTED.value)
        try:
            checkpoint = DispatchCheckpoint(checkpoint_raw)
        except ValueError as exc:
            raise InvalidDispatchCheckpointError(f"unrecognized checkpoint: {checkpoint_raw!r}") from exc

        attempt = data.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
            raise ExecutionDeserializationError(f"invalid field: 'attempt' (got {attempt!r})")

        publisher = data.get("publisher")
        if not isinstance(publisher, str) or not publisher:
            raise ExecutionDeserializationError(f"invalid field: 'publisher' (got {publisher!r})")

        for name in ("lease", "platform_state", "result", "failure", "dispatch_authorization"):
            value = data.get(name)
            if value is not None and not isinstance(value, dict):
                raise ExecutionDeserializationError(f"invalid field: {name!r} (got {value!r})")

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise ExecutionDeserializationError(f"invalid field: 'metadata' (got {metadata!r})")

        return cls(
            execution_id=data["execution_id"],
            publication_id=data["publication_id"],
            workflow_id=data["workflow_id"],
            channel=channel,
            status=status,
            checkpoint=checkpoint,
            attempt=attempt,
            publisher=publisher,
            lease=data.get("lease"),
            platform_state=data.get("platform_state"),
            result=data.get("result"),
            failure=data.get("failure"),
            dispatch_authorization=data.get("dispatch_authorization"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            metadata=metadata,
            schema_version=schema_version,
        )
