"""Domain models for Claude media analysis: the structured result Claude
returns, and the versioned document persisted to S3 at analysis/<workflow_id>.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    AnalysisDeserializationError,
    AnalysisVersionMismatchError,
    InvalidAnalysisStatusError,
)

CURRENT_DOCUMENT_VERSION = 1
SUPPORTED_DOCUMENT_VERSIONS = {1}

_RESULT_STRING_FIELD = "summary"
_RESULT_ARRAY_FIELDS = (
    "visible_subjects",
    "visual_style",
    "dominant_themes",
    "brand_signals",
    "content_opportunities",
    "quality_observations",
    "safety_notes",
)


class AnalysisStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AnalysisResult:
    """Claude's structured analysis of one piece of media. Every field is
    meant to hold direct observations or explicitly-flagged interpretation
    — never invented brand names, identities, locations, or performance
    claims (enforced by the prompt, not by this shape)."""

    summary: str
    visible_subjects: list[str]
    visual_style: list[str]
    dominant_themes: list[str]
    brand_signals: list[str]
    content_opportunities: list[str]
    quality_observations: list[str]
    safety_notes: list[str]

    def to_dict(self) -> dict:
        return {
            _RESULT_STRING_FIELD: self.summary,
            **{name: getattr(self, name) for name in _RESULT_ARRAY_FIELDS},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        summary = data.get(_RESULT_STRING_FIELD)
        if not isinstance(summary, str):
            raise AnalysisDeserializationError(f"invalid field: 'summary' (got {summary!r})")

        values: dict[str, list[str]] = {}
        for name in _RESULT_ARRAY_FIELDS:
            value = data.get(name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise AnalysisDeserializationError(f"invalid field: {name!r} (got {value!r})")
            values[name] = value

        return cls(summary=summary, **values)


@dataclass(frozen=True)
class AnalysisUsage:
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisUsage":
        input_tokens = data.get("input_tokens")
        output_tokens = data.get("output_tokens")
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
            raise AnalysisDeserializationError(f"invalid field: 'input_tokens' (got {input_tokens!r})")
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
            raise AnalysisDeserializationError(f"invalid field: 'output_tokens' (got {output_tokens!r})")
        return cls(input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True)
class AnalysisDocument:
    analysis_id: str
    workflow_id: str
    created_at: str
    updated_at: str
    status: AnalysisStatus
    schema_version: int
    prompt_version: int
    model: str
    media_type: str
    result: AnalysisResult | None = None
    usage: AnalysisUsage | None = None
    metadata: dict = field(default_factory=dict)
    version: int = CURRENT_DOCUMENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "analysis_id": self.analysis_id,
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "schema_version": self.schema_version,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "media_type": self.media_type,
            "result": self.result.to_dict() if self.result is not None else None,
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisDocument":
        if not isinstance(data, dict):
            raise AnalysisDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_DOCUMENT_VERSIONS:
            raise AnalysisVersionMismatchError(
                f"unsupported analysis document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_DOCUMENT_VERSIONS)})"
            )

        required_strings = ("analysis_id", "workflow_id", "created_at", "updated_at", "model", "media_type")
        for name in required_strings:
            value = data.get(name)
            if not isinstance(value, str) or not value:
                raise AnalysisDeserializationError(f"missing or invalid field: {name!r}")

        status_raw = data.get("status")
        try:
            status = AnalysisStatus(status_raw)
        except ValueError as exc:
            raise InvalidAnalysisStatusError(f"unrecognized status: {status_raw!r}") from exc

        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise AnalysisDeserializationError(f"invalid field: 'schema_version' (got {schema_version!r})")

        prompt_version = data.get("prompt_version")
        if not isinstance(prompt_version, int) or isinstance(prompt_version, bool):
            raise AnalysisDeserializationError(f"invalid field: 'prompt_version' (got {prompt_version!r})")

        result_raw = data.get("result")
        result = AnalysisResult.from_dict(result_raw) if result_raw is not None else None

        usage_raw = data.get("usage")
        usage = AnalysisUsage.from_dict(usage_raw) if usage_raw is not None else None

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise AnalysisDeserializationError(f"invalid field: 'metadata' (got {metadata!r})")

        return cls(
            analysis_id=data["analysis_id"],
            workflow_id=data["workflow_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=status,
            schema_version=schema_version,
            prompt_version=prompt_version,
            model=data["model"],
            media_type=data["media_type"],
            result=result,
            usage=usage,
            metadata=metadata,
            version=version,
        )
