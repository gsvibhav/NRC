"""Domain models for the adaptive clarification engine (Milestone 4B):
the structured working-context model accumulated across a conversation,
and the decision Claude returns each cycle.

Two distinct "context" concepts exist in this codebase and must not be
confused: `src/ai/models.py`'s `AnalysisResult` is what Claude *observed or
inferred from the media itself* (immutable once persisted, lives solely in
`analysis/<workflow_id>.json`). `ClarificationContext` here is the
*evolving operational understanding* built from that analysis plus every
explicit user answer — it lives on the workflow document
(`WorkflowDocument.clarification_context`, see workflow/models.py) because
it changes every turn and is scoped to this one workflow's conversation,
not to the media analysis itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import ClarificationResponseSchemaMismatchError

CLARIFICATION_CONTEXT_VERSION = 1

# The named categories the engine tracks explicitly. This is a catalogue of
# information *categories*, not a mandatory questionnaire — see
# clarification_prompts.py's system prompt for the explicit instruction
# that Claude must decide dynamically which of these (if any) still need
# asking about, never iterate through them in order.
_SCALAR_FIELDS = (
    "brand_name",
    "content_type",
    "objective",
    "audience",
    "message_focus",
    "tone",
    "call_to_action",
)


class ContextSource(str, Enum):
    """Where a context value came from — used only to decide whose value
    wins on conflict (see ClarificationContext.apply_updates); never shown
    to the user."""

    ANALYSIS = "analysis"
    INFERENCE = "inference"
    USER = "user"


@dataclass(frozen=True)
class ContextField:
    """One known value plus enough provenance to resolve future conflicts.
    `value` is a string for every field in _SCALAR_FIELDS, or a list[str]
    for `platforms`."""

    value: object
    source: ContextSource
    updated_at: str

    def to_dict(self) -> dict:
        return {"value": self.value, "source": self.source.value, "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, data: dict, *, expect_list: bool = False) -> "ContextField":
        if not isinstance(data, dict):
            raise ClarificationResponseSchemaMismatchError(f"expected an object for a context field, got {data!r}")

        value = data.get("value")
        if expect_list:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ClarificationResponseSchemaMismatchError(f"invalid context field value: {value!r}")
        elif not isinstance(value, str):
            raise ClarificationResponseSchemaMismatchError(f"invalid context field value: {value!r}")

        source_raw = data.get("source")
        try:
            source = ContextSource(source_raw)
        except ValueError as exc:
            raise ClarificationResponseSchemaMismatchError(f"unrecognized context field source: {source_raw!r}") from exc

        updated_at = data.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise ClarificationResponseSchemaMismatchError("missing or invalid context field 'updated_at'")

        return cls(value=value, source=source, updated_at=updated_at)


@dataclass(frozen=True)
class ClarificationContext:
    """The accumulated working understanding of one workflow's post,
    versioned and persisted inside the workflow document. Not a mandatory
    schema — every field may legitimately stay `None`/empty for the entire
    conversation if the media and a single answer already cover
    everything needed."""

    version: int = CLARIFICATION_CONTEXT_VERSION
    brand_name: ContextField | None = None
    content_type: ContextField | None = None
    objective: ContextField | None = None
    audience: ContextField | None = None
    message_focus: ContextField | None = None
    tone: ContextField | None = None
    call_to_action: ContextField | None = None
    platforms: ContextField | None = None
    factual_context: dict = field(default_factory=dict)
    user_preferences: dict = field(default_factory=dict)
    unresolved: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            **{
                name: (getattr(self, name).to_dict() if getattr(self, name) is not None else None)
                for name in (*_SCALAR_FIELDS, "platforms")
            },
            "factual_context": self.factual_context,
            "user_preferences": self.user_preferences,
            "unresolved": self.unresolved,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClarificationContext":
        if not isinstance(data, dict):
            raise ClarificationResponseSchemaMismatchError(f"expected a JSON object, got {type(data).__name__}")

        kwargs: dict = {"version": data.get("version", CLARIFICATION_CONTEXT_VERSION)}
        for name in _SCALAR_FIELDS:
            raw = data.get(name)
            kwargs[name] = ContextField.from_dict(raw) if raw is not None else None

        platforms_raw = data.get("platforms")
        kwargs["platforms"] = ContextField.from_dict(platforms_raw, expect_list=True) if platforms_raw is not None else None

        factual_context = data.get("factual_context", {})
        if not isinstance(factual_context, dict) or not all(isinstance(v, str) for v in factual_context.values()):
            raise ClarificationResponseSchemaMismatchError(f"invalid 'factual_context': {factual_context!r}")
        kwargs["factual_context"] = factual_context

        user_preferences = data.get("user_preferences", {})
        if not isinstance(user_preferences, dict) or not all(isinstance(v, str) for v in user_preferences.values()):
            raise ClarificationResponseSchemaMismatchError(f"invalid 'user_preferences': {user_preferences!r}")
        kwargs["user_preferences"] = user_preferences

        unresolved = data.get("unresolved", [])
        if not isinstance(unresolved, list) or not all(isinstance(item, str) for item in unresolved):
            raise ClarificationResponseSchemaMismatchError(f"invalid 'unresolved': {unresolved!r}")
        kwargs["unresolved"] = unresolved

        return cls(**kwargs)

    def apply_updates(
        self, updates: dict, *, unresolved: list, updated_at: str, source: ContextSource
    ) -> "ClarificationContext":
        """Return a new context with `updates` (raw values from a parsed
        ClarificationDecision.context_updates) merged in, attributed to
        `source` — USER when this cycle interpreted an explicit reply,
        INFERENCE when it's the first cycle reading only the media
        analysis (see clarification_service.py, the sole caller, which
        picks `source` based on whether a user answer was present this
        cycle).

        Explicit user answers always win: a field already recorded with
        source=USER is never silently replaced by an INFERENCE/ANALYSIS
        update — "do not overwrite previous explicit answers silently."
        A *new* USER-sourced update always applies, even over a prior
        USER value, since that's a genuine correction (see docs on
        handling contradictions) and the latest explicit statement is
        authoritative.
        """

        new_fields = {}
        for name in _SCALAR_FIELDS:
            raw_value = updates.get(name)
            existing = getattr(self, name)
            if not raw_value:
                new_fields[name] = existing
                continue
            if existing is not None and existing.source is ContextSource.USER and source is not ContextSource.USER:
                new_fields[name] = existing  # never silently downgrade an explicit answer
                continue
            new_fields[name] = ContextField(value=raw_value, source=source, updated_at=updated_at)

        platforms_value = updates.get("platforms")
        existing_platforms = self.platforms
        if not platforms_value:
            new_fields["platforms"] = existing_platforms
        elif existing_platforms is not None and existing_platforms.source is ContextSource.USER and source is not ContextSource.USER:
            new_fields["platforms"] = existing_platforms
        else:
            new_fields["platforms"] = ContextField(value=list(platforms_value), source=source, updated_at=updated_at)

        merged_facts = {**self.factual_context, **{k: v for k, v in (updates.get("factual_context") or {}).items() if v}}
        merged_preferences = {
            **self.user_preferences,
            **{k: v for k, v in (updates.get("user_preferences") or {}).items() if v},
        }

        return ClarificationContext(
            version=self.version,
            **new_fields,
            factual_context=merged_facts,
            user_preferences=merged_preferences,
            unresolved=list(unresolved),
        )


class ClarificationDecisionType(str, Enum):
    ASK_QUESTION = "ASK_QUESTION"
    CONTINUE = "CONTINUE"


@dataclass(frozen=True)
class ClarificationQuestion:
    """A question as Claude proposed it — before validation
    (question_validation.py) and before a question_id is assigned
    (clarification_service.py, only once validation passes)."""

    text: str
    purpose: str
    target_field: str


@dataclass(frozen=True)
class ClarificationDecision:
    """One parsed, schema-validated Claude clarification response —
    always exactly one of ASK_QUESTION (with `question` set) or CONTINUE
    (with `question` None)."""

    decision: ClarificationDecisionType
    context_sufficient: bool
    question: ClarificationQuestion | None
    context_updates: dict
    remaining_uncertainties: list
    confidence: float
