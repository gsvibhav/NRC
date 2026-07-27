"""Exceptions for workflow persistence.

Every exception carries a `user_message` — short and generic, per
docs/WORKFLOW.md's error-handling principles — mirroring the pattern
already used in src/media/errors.py. Callers show `user_message` to the
Telegram user and log the exception itself for diagnosis.
"""

from __future__ import annotations


class WorkflowError(Exception):
    """Base class for every expected failure in workflow persistence."""

    user_message = "Sorry, something went wrong saving your post's status. Please try again."

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class WorkflowNotFoundError(WorkflowError):
    user_message = "I couldn't find that post. It may have expired or never existed."


class WorkflowValidationError(WorkflowError):
    """Base class for a workflow document that isn't structurally valid."""


class WorkflowDeserializationError(WorkflowValidationError):
    """The stored document isn't valid JSON, or is missing/misshapen fields."""


class WorkflowSerializationError(WorkflowValidationError):
    """The in-memory document can't be encoded as JSON."""


class WorkflowVersionMismatchError(WorkflowValidationError):
    """The document's `version` field isn't one this code knows how to read."""


class InvalidWorkflowStateError(WorkflowValidationError):
    """A `state` value isn't one of the recognized WorkflowState members."""


class WorkflowPersistenceError(WorkflowError):
    """A read or write to the workflow store failed for a reason other than
    a precondition (see WorkflowConcurrentModificationError) or a missing
    object (see WorkflowNotFoundError)."""


class WorkflowConcurrentModificationError(WorkflowError):
    """An optimistic-concurrency precondition failed on write.

    Either the workflow already existed when the caller expected to create
    a new one, or it was modified by someone else since the caller last
    loaded it. See src/workflow/manager.py for the concurrency strategy.
    """

    user_message = "That post was just updated elsewhere. Please try again."
