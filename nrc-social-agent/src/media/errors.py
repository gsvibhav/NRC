"""Exceptions for the media ingestion pipeline.

Every exception here carries a `user_message` — short and generic by design,
per docs/WORKFLOW.md's error-handling principles. Callers show `user_message`
to the Telegram user and log the exception itself (with full context) for
diagnosis. Never show the user anything beyond `user_message`.
"""

from __future__ import annotations


class MediaIngestionError(Exception):
    """Base class for every expected failure in the ingestion pipeline."""

    user_message = "Sorry, something went wrong processing your media. Please try again."

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NoMediaError(MediaIngestionError):
    user_message = "I can only accept photos or videos right now."


class UnsupportedMediaTypeError(MediaIngestionError):
    user_message = (
        "That media format isn't supported. Please send a standard photo or video."
    )


class MediaTooLargeError(MediaIngestionError):
    user_message = "That file is too large. Please send a smaller photo or video."


class UnknownMediaSizeError(MediaIngestionError):
    user_message = "I couldn't verify that file's size, so I can't accept it. Please resend it."


class MissingTelegramFileError(MediaIngestionError):
    user_message = "I couldn't find a downloadable file in your message. Please resend it."


class TelegramDownloadError(MediaIngestionError):
    user_message = "I couldn't download your media from Telegram. Please try again."


class StorageUploadError(MediaIngestionError):
    user_message = "I couldn't save your media. Please try again shortly."


class WorkflowRecordError(MediaIngestionError):
    """Raised when the media upload succeeded but its workflow record
    couldn't be created/persisted (see src/workflow/errors.py)."""

    user_message = "Your media was uploaded, but I couldn't save its status. Please try again."


class ConversationRecordError(MediaIngestionError):
    """Raised when the workflow record was created but the conversation's
    active-workflow pointer couldn't be set (see src/conversation/errors.py).
    Without that pointer, /status and /cancel won't be able to find this
    workflow, so this is treated as an ingestion failure."""

    user_message = "Your media was uploaded, but I couldn't save its status. Please try again."
