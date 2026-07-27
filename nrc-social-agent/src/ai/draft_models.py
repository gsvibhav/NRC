"""Domain models for the Primary Draft Generation Engine (Milestone 6) and
the Telegram Draft Review, Editing and Approval Workflow (Milestone 7): the
output-specific content shape(s) and the persisted draft envelope.

Only one output-specific content model exists so far —
`InstagramReelCaptionContent`, the sole `supports_generation = True`
registry entry (see content_plan_models.py). A future milestone adding
generation support for another output type should add its own content
dataclass here (or a sibling module, if the number grows large) rather
than forcing every format into one generic shape — captions and, say, a
future website case-study outline have genuinely different fields.

`DraftDocument` is reused, not duplicated, as the envelope for Milestone
7's immutable per-version records (see draft_version_models.py/
draft_version_repository.py) — a version *is* a `DraftDocument`, just one
persisted at a versioned S3 key instead of the flat Milestone-6 path, with
`version_number`/`parent_version_number`/`edit_instruction_reference`
populated. Document schema bumped 1 -> 2 for these three new fields; a
version-1 document (any Milestone-6-era flat draft, migrated or not)
reads back with `version_number=1`, `parent_version_number=None`,
`edit_instruction_reference=None` — exactly the values a true first
version should have anyway, so no special-casing is needed once migrated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    DraftResponseSchemaMismatchError,
    DraftVersionMismatchError,
    InvalidDraftStatusError,
)

CURRENT_DOCUMENT_VERSION = 2
SUPPORTED_DOCUMENT_VERSIONS = {1, 2}


class DraftStatus(str, Enum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FAILED = "FAILED"


@dataclass(frozen=True)
class InstagramReelCaptionContent:
    """The sole generation-supported content shape as of Milestone 6.
    `hashtags` and `cta` are optional and commonly empty/`None` — neither
    is added by default (see README.md's "Hashtag policy" / "CTA
    handling"); their presence is a per-draft strategic choice, not a
    schema requirement."""

    caption: str
    hashtags: list = field(default_factory=list)
    cta: str | None = None

    def to_dict(self) -> dict:
        return {"caption": self.caption, "hashtags": self.hashtags, "cta": self.cta}

    @classmethod
    def from_dict(cls, data: dict) -> "InstagramReelCaptionContent":
        if not isinstance(data, dict):
            raise DraftResponseSchemaMismatchError(f"expected an object for 'content', got {data!r}")

        caption = data.get("caption")
        if not isinstance(caption, str) or not caption:
            raise DraftResponseSchemaMismatchError(f"invalid field: 'content.caption' (got {caption!r})")

        hashtags = data.get("hashtags", [])
        if not isinstance(hashtags, list) or not all(isinstance(h, str) for h in hashtags):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'content.hashtags' (got {hashtags!r})")

        cta = data.get("cta")
        if cta is not None and not isinstance(cta, str):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'content.cta' (got {cta!r})")

        return cls(caption=caption, hashtags=hashtags, cta=cta or None)


# Maps an OutputType value to the content dataclass that knows how to
# read/write it — extend this (and add a sibling dataclass above) the day
# a second output type gains supports_generation = True. Kept as a plain
# dict, not a registry-of-registries — a single well-named mapping is
# clearer than another abstraction layer for exactly one entry.
CONTENT_MODELS_BY_OUTPUT_TYPE = {
    "instagram_reel_caption": InstagramReelCaptionContent,
}


@dataclass(frozen=True)
class DraftSourceVersions:
    """What produced this draft, for a future editing/regeneration
    milestone to reason about — never a copy of the source documents
    themselves, just their version numbers."""

    analysis_schema_version: int
    content_plan_schema_version: int
    draft_prompt_version: int
    clarification_context_version: int | None = None

    def to_dict(self) -> dict:
        return {
            "analysis_schema_version": self.analysis_schema_version,
            "clarification_context_version": self.clarification_context_version,
            "content_plan_schema_version": self.content_plan_schema_version,
            "draft_prompt_version": self.draft_prompt_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftSourceVersions":
        if not isinstance(data, dict):
            raise DraftResponseSchemaMismatchError(f"expected an object for 'source_versions', got {data!r}")

        for name in ("analysis_schema_version", "content_plan_schema_version", "draft_prompt_version"):
            value = data.get(name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise DraftResponseSchemaMismatchError(f"invalid field: 'source_versions.{name}' (got {value!r})")

        clarification_context_version = data.get("clarification_context_version")
        if clarification_context_version is not None and (
            not isinstance(clarification_context_version, int) or isinstance(clarification_context_version, bool)
        ):
            raise DraftResponseSchemaMismatchError("invalid field: 'source_versions.clarification_context_version'")

        return cls(
            analysis_schema_version=data["analysis_schema_version"],
            content_plan_schema_version=data["content_plan_schema_version"],
            draft_prompt_version=data["draft_prompt_version"],
            clarification_context_version=clarification_context_version,
        )


@dataclass(frozen=True)
class DraftUsage:
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    @classmethod
    def from_dict(cls, data: dict) -> "DraftUsage":
        input_tokens = data.get("input_tokens")
        output_tokens = data.get("output_tokens")
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'usage.input_tokens' (got {input_tokens!r})")
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'usage.output_tokens' (got {output_tokens!r})")
        return cls(input_tokens=input_tokens, output_tokens=output_tokens)


_REQUIRED_STRING_FIELDS = (
    "draft_id", "workflow_id", "plan_id", "output_id", "output_type", "created_at", "updated_at", "model",
)


@dataclass(frozen=True)
class DraftDocument:
    """The persisted draft envelope, at drafts/<workflow_id>/<output_id>.json
    (see draft_manager.py). `content` is a plain dict at this layer —
    output-type-specific validation (via CONTENT_MODELS_BY_OUTPUT_TYPE)
    happens in the generation/parsing layer, the same "generic dict at the
    envelope layer, structured dataclass one layer up" pattern already
    used for WorkflowDocument.media/analysis."""

    draft_id: str
    workflow_id: str
    plan_id: str
    output_id: str
    output_type: str
    created_at: str
    updated_at: str
    status: DraftStatus
    schema_version: int
    prompt_version: int
    model: str
    content: dict | None = None
    source_versions: dict | None = None
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)
    version_number: int = 1
    parent_version_number: int | None = None
    edit_instruction_reference: dict | None = None
    version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "draft_id": self.draft_id,
            "workflow_id": self.workflow_id,
            "plan_id": self.plan_id,
            "output_id": self.output_id,
            "output_type": self.output_type,
            "status": self.status.value,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "content": self.content,
            "source_versions": self.source_versions,
            "usage": self.usage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "version_number": self.version_number,
            "parent_version_number": self.parent_version_number,
            "edit_instruction_reference": self.edit_instruction_reference,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DraftDocument":
        if not isinstance(data, dict):
            raise DraftResponseSchemaMismatchError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise DraftVersionMismatchError(
                f"unsupported draft document version: {version!r} (supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        for name in _REQUIRED_STRING_FIELDS:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise DraftResponseSchemaMismatchError(f"missing or invalid field: {name!r}")

        status_raw = data.get("status")
        try:
            status = DraftStatus(status_raw)
        except ValueError as exc:
            raise InvalidDraftStatusError(f"unrecognized status: {status_raw!r}") from exc

        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'schema_version' (got {schema_version!r})")

        prompt_version = data.get("prompt_version")
        if not isinstance(prompt_version, int) or isinstance(prompt_version, bool):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'prompt_version' (got {prompt_version!r})")

        content = data.get("content")
        if content is not None and not isinstance(content, dict):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'content' (got {content!r})")

        source_versions = data.get("source_versions")
        if source_versions is not None and not isinstance(source_versions, dict):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'source_versions' (got {source_versions!r})")

        usage = data.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'usage' (got {usage!r})")

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise DraftResponseSchemaMismatchError(f"invalid field: 'metadata' (got {metadata!r})")

        # Absent entirely on a version-1 (Milestone 6-era) document — a
        # flat draft is always, by definition, the first and only version.
        version_number = data.get("version_number", 1)
        if not isinstance(version_number, int) or isinstance(version_number, bool) or version_number < 1:
            raise DraftResponseSchemaMismatchError(f"invalid field: 'version_number' (got {version_number!r})")

        parent_version_number = data.get("parent_version_number")
        if parent_version_number is not None and (
            not isinstance(parent_version_number, int) or isinstance(parent_version_number, bool)
        ):
            raise DraftResponseSchemaMismatchError(
                f"invalid field: 'parent_version_number' (got {parent_version_number!r})"
            )

        edit_instruction_reference = data.get("edit_instruction_reference")
        if edit_instruction_reference is not None and not isinstance(edit_instruction_reference, dict):
            raise DraftResponseSchemaMismatchError(
                f"invalid field: 'edit_instruction_reference' (got {edit_instruction_reference!r})"
            )

        return cls(
            draft_id=data["draft_id"],
            workflow_id=data["workflow_id"],
            plan_id=data["plan_id"],
            output_id=data["output_id"],
            output_type=data["output_type"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=data["model"],
            content=content,
            source_versions=source_versions,
            usage=usage,
            metadata=metadata,
            version_number=version_number,
            parent_version_number=parent_version_number,
            edit_instruction_reference=edit_instruction_reference,
            version=version,
        )
