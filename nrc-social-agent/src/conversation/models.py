"""The persisted conversation document.

One JSON object per Telegram user, stored at conversation/<telegram_user_id>
.json (see store.py). Deliberately minimal — it holds only the active
workflow pointer and its own bookkeeping, never a copy of anything already
stored in the workflow document itself (see README.md's schema for why).
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    ConversationDeserializationError,
    ConversationVersionMismatchError,
    InvalidConversationStateError,
)
from .states import ConversationState

CURRENT_VERSION = 1
SUPPORTED_VERSIONS = {1}


@dataclass(frozen=True)
class ConversationDocument:
    telegram_user_id: int
    active_workflow_id: str | None
    state: ConversationState
    updated_at: str
    version: int = CURRENT_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "telegram_user_id": self.telegram_user_id,
            "active_workflow_id": self.active_workflow_id,
            "state": self.state.value,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationDocument":
        if not isinstance(data, dict):
            raise ConversationDeserializationError(f"expected a JSON object, got {type(data).__name__}")

        version = data.get("version")
        if version not in SUPPORTED_VERSIONS:
            raise ConversationVersionMismatchError(
                f"unsupported conversation document version: {version!r} "
                f"(supported: {sorted(SUPPORTED_VERSIONS)})"
            )

        telegram_user_id = data.get("telegram_user_id")
        if not isinstance(telegram_user_id, int) or isinstance(telegram_user_id, bool):
            raise ConversationDeserializationError(
                f"missing or invalid field: 'telegram_user_id' (got {telegram_user_id!r})"
            )

        updated_at = data.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise ConversationDeserializationError("missing or invalid field: 'updated_at'")

        active_workflow_id = data.get("active_workflow_id")
        if active_workflow_id is not None and not isinstance(active_workflow_id, str):
            raise ConversationDeserializationError(
                f"invalid field: 'active_workflow_id' (got {active_workflow_id!r})"
            )

        state_raw = data.get("state")
        try:
            state = ConversationState(state_raw)
        except ValueError as exc:
            raise InvalidConversationStateError(f"unrecognized state: {state_raw!r}") from exc

        return cls(
            telegram_user_id=telegram_user_id,
            active_workflow_id=active_workflow_id,
            state=state,
            updated_at=updated_at,
            version=version,
        )
