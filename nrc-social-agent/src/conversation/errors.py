"""Exceptions for conversation persistence and workflow resolution.

Every exception carries a `user_message` — short and generic, mirroring the
pattern already used in src/workflow/errors.py and src/media/errors.py.
Callers show `user_message` to the Telegram user and log the exception
itself for diagnosis.
"""

from __future__ import annotations


class ConversationError(Exception):
    """Base class for every expected failure in conversation persistence
    and resolution."""

    user_message = "Sorry, something went wrong. Please try again."

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ConversationNotFoundError(ConversationError):
    """No conversation record exists yet for this Telegram user."""

    user_message = "No active post right now — send a photo or video to begin."


class ConversationValidationError(ConversationError):
    """Base class for a conversation document that isn't structurally valid."""


class ConversationDeserializationError(ConversationValidationError):
    """The stored document isn't valid JSON, or is missing/misshapen fields."""


class ConversationSerializationError(ConversationValidationError):
    """The in-memory document can't be encoded as JSON."""


class ConversationVersionMismatchError(ConversationValidationError):
    """The document's `version` field isn't one this code knows how to read."""


class InvalidConversationStateError(ConversationValidationError):
    """A `state` value isn't one of the recognized ConversationState members."""


class ConversationPersistenceError(ConversationError):
    """A read or write to the conversation store failed for a reason other
    than a precondition or a missing object."""


class ConversationConcurrentModificationError(ConversationError):
    """An optimistic-concurrency precondition failed on write — the
    conversation record was modified by someone else since it was last
    loaded. See src/conversation/manager.py for the concurrency strategy."""

    user_message = "That post was just updated elsewhere. Please try again."


class NoActiveWorkflowError(ConversationError):
    """Raised by resolution when there's nothing to resolve — whether
    because no conversation record exists yet, or the conversation exists
    but currently has no active workflow pointer."""

    user_message = "No active post right now — send a photo or video to begin."


class StaleWorkflowPointerError(ConversationError):
    """Raised by resolution when the conversation's active_workflow_id
    references a workflow that no longer exists in the workflow store."""

    user_message = "I couldn't find that post anymore. Please start a new one."
