"""Domain model for the current-version pointer (Milestone 7): the small,
mutable record that names which immutable `DraftDocument` version (see
draft_models.py) is authoritative for a given (workflow_id, output_id)
right now.

This is the one place review-lifecycle status lives. It is deliberately
*not* stored on the immutable version documents themselves — those stay
frozen exactly as generated/edited, forever, once persisted (see
draft_version_manager.py); "approved", "saved as draft", and "rejected"
are properties of the *review*, not of the *content*, so they belong on
this pointer, which is designed to be mutated (via optimistic concurrency)
as the review progresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    DraftVersionDeserializationError,
    DraftVersionDocumentVersionMismatchError,
)

CURRENT_DOCUMENT_VERSION = 1
SUPPORTED_DOCUMENT_VERSIONS = {1}


class ReviewStatus(str, Enum):
    """The current pointer's own lifecycle position — distinct from
    `DraftStatus` (draft_models.py), which describes whether a single
    generation/edit *attempt* succeeded, not where the review stands."""

    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    SAVED_AS_DRAFT = "SAVED_AS_DRAFT"
    REJECTED = "REJECTED"


_REQUIRED_STRING_FIELDS = ("workflow_id", "output_id", "current_draft_id", "updated_at")


@dataclass(frozen=True)
class CurrentDraftPointer:
    workflow_id: str
    output_id: str
    current_draft_id: str
    current_version_number: int
    status: ReviewStatus
    updated_at: str
    approved_at: str | None = None
    approved_by_telegram_user_id: int | None = None
    metadata: dict = field(default_factory=dict)
    version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "workflow_id": self.workflow_id,
            "output_id": self.output_id,
            "current_draft_id": self.current_draft_id,
            "current_version_number": self.current_version_number,
            "status": self.status.value,
            "updated_at": self.updated_at,
            "approved_at": self.approved_at,
            "approved_by_telegram_user_id": self.approved_by_telegram_user_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CurrentDraftPointer":
        if not isinstance(data, dict):
            raise DraftVersionDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise DraftVersionDocumentVersionMismatchError(
                f"unsupported current-pointer document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        for name in _REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise DraftVersionDeserializationError(f"missing or invalid field: {name!r}")

        current_version_number = data.get("current_version_number")
        if not isinstance(current_version_number, int) or isinstance(current_version_number, bool) or current_version_number < 1:
            raise DraftVersionDeserializationError(
                f"invalid field: 'current_version_number' (got {current_version_number!r})"
            )

        status_raw = data.get("status")
        try:
            status = ReviewStatus(status_raw)
        except ValueError as exc:
            raise DraftVersionDeserializationError(f"unrecognized status: {status_raw!r}") from exc

        approved_at = data.get("approved_at")
        if approved_at is not None and not isinstance(approved_at, str):
            raise DraftVersionDeserializationError(f"invalid field: 'approved_at' (got {approved_at!r})")

        approved_by = data.get("approved_by_telegram_user_id")
        if approved_by is not None and (not isinstance(approved_by, int) or isinstance(approved_by, bool)):
            raise DraftVersionDeserializationError(
                f"invalid field: 'approved_by_telegram_user_id' (got {approved_by!r})"
            )

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise DraftVersionDeserializationError(f"invalid field: 'metadata' (got {metadata!r})")

        return cls(
            workflow_id=data["workflow_id"],
            output_id=data["output_id"],
            current_draft_id=data["current_draft_id"],
            current_version_number=current_version_number,
            status=status,
            updated_at=data["updated_at"],
            approved_at=approved_at,
            approved_by_telegram_user_id=approved_by,
            metadata=metadata,
            version=version,
        )
