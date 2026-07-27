"""Top-level orchestration for single-pass media analysis.

Wires together — without redefining any of them — the existing S3Storage
adapter (retrieves the already-uploaded media), the Claude adapter
(client.py), response parsing (parser.py), analysis persistence
(analysis_manager.py), and the existing WorkflowManager / ConversationManager
(attaches a lightweight reference to the workflow and, on failure, clears
the active-workflow pointer — exactly what /cancel already does for a
rejected workflow; see src/handlers.py).

Workflow-state design note: docs/WORKFLOW.md's only documented exits from
ANALYZING_MEDIA are WAITING_FOR_USER (follow-up question) and
GENERATING_CONTENT (caption generation) — both explicitly out of scope for
this milestone. Rather than invent a new state or misuse one of those two,
a successful single-pass analysis leaves `state` unchanged at
ANALYZING_MEDIA; the real outcome is recorded in the attached
`analysis` reference (`status: "COMPLETED"`). See README.md for the full
reasoning and the flagged documentation gap this surfaces for a future
milestone. A failed analysis (including "video not supported") does use an
already-documented, existing exit: ANALYZING_MEDIA -> FAILED, per
docs/WORKFLOW.md §7.3.

Retryable vs. permanent failures (Milestone 4B): a ClaudeRetryableError
(timeout/rate-limit/transient — the SDK's own retries already exhausted)
does NOT move the workflow to FAILED or clear the active pointer; it sets
`metadata["pending_retry"] = "analysis"` so `/retry` (see handlers.py) can
safely re-attempt. Every other AnalysisError is treated as permanent:
FAILED, pointer cleared, per the module docstring above.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

from ..conversation.lifecycle import clear_pointer_if_terminal
from ..conversation.manager import ConversationManager
from ..media.download import cleanup_temp_file
from ..media.types import MediaType
from ..storage.s3_storage import S3DownloadError, S3Storage
from ..workflow.errors import WorkflowError
from ..workflow.manager import WorkflowManager
from ..workflow.states import WorkflowState
from .analysis_manager import AnalysisManager
from .client import ClaudeClient
from .errors import (
    AnalysisError,
    ClaudeRetryableError,
    MediaRetrievalError,
    MediaTooLargeForAnalysisError,
    UnsupportedMediaTypeForAnalysisError,
)
from .models import AnalysisDocument, AnalysisStatus, AnalysisUsage
from .parser import parse_analysis_response
from .prompts import ANALYSIS_PROMPT_VERSION

logger = logging.getLogger(__name__)

# Claude's own base64-encoded request-body limit for direct-API image
# submission is 10 MB (see README.md's "Analysis" section). Base64 inflates
# raw bytes by ~4/3, so the raw-file ceiling is set well under that to
# leave headroom for the rest of the request body.
CLAUDE_MAX_IMAGE_RAW_BYTES = 7 * 1024 * 1024  # ~7 MB raw -> ~9.3 MB base64

_ANALYZABLE_MEDIA_TYPES = frozenset({MediaType.PHOTO.value})


@dataclass(frozen=True)
class AnalysisOutcome:
    workflow_id: str
    status: AnalysisStatus
    summary: str | None


class AnalysisService:
    def __init__(
        self,
        *,
        model: str,
        schema_version: int,
        storage: S3Storage,
        claude_client: ClaudeClient,
        analysis_manager: AnalysisManager,
        workflow_manager: WorkflowManager,
        conversation_manager: ConversationManager,
    ) -> None:
        self._model = model
        self._schema_version = schema_version
        self._storage = storage
        self._claude_client = claude_client
        self._analysis_manager = analysis_manager
        self._workflow_manager = workflow_manager
        self._conversation_manager = conversation_manager

    async def analyze_workflow(self, *, workflow_id: str, telegram_user_id: int) -> AnalysisOutcome:
        """Run (or reuse) analysis for `workflow_id`. Raises an
        AnalysisError subclass on any expected failure; each carries a
        short, generic `user_message`. Never returns a result unless the
        analysis actually completed successfully."""

        logger.info("Analysis requested workflow_id=%s telegram_user_id=%s", workflow_id, telegram_user_id)

        try:
            workflow_document = self._workflow_manager.load_workflow(workflow_id)
        except WorkflowError as exc:
            raise AnalysisError(f"failed to load workflow_id={workflow_id}: {exc}") from exc

        existing = self._analysis_manager.find_existing(workflow_id)
        if existing is not None and existing.status is AnalysisStatus.COMPLETED:
            logger.info("Duplicate analysis skipped workflow_id=%s (already COMPLETED)", workflow_id)
            summary = existing.result.summary if existing.result else None
            return AnalysisOutcome(workflow_id=workflow_id, status=AnalysisStatus.COMPLETED, summary=summary)

        media = workflow_document.media
        media_type = media.get("media_type") or "unknown"
        s3_key = media.get("s3_key")
        mime_type = media.get("mime_type") or "image/jpeg"
        reported_file_size = media.get("file_size")

        try:
            if media_type not in _ANALYZABLE_MEDIA_TYPES:
                raise UnsupportedMediaTypeForAnalysisError(
                    f"media_type={media_type!r} is not analyzable in this milestone"
                )

            if isinstance(reported_file_size, int) and reported_file_size > CLAUDE_MAX_IMAGE_RAW_BYTES:
                raise MediaTooLargeForAnalysisError(
                    f"reported file_size={reported_file_size} exceeds the safe submission limit"
                )

            logger.info("Media retrieval started workflow_id=%s", workflow_id)
            image_bytes = self._retrieve_media(s3_key, workflow_id)
            logger.info("Media retrieval completed workflow_id=%s bytes=%d", workflow_id, len(image_bytes))

            if len(image_bytes) > CLAUDE_MAX_IMAGE_RAW_BYTES:
                raise MediaTooLargeForAnalysisError(
                    f"actual size={len(image_bytes)} bytes exceeds the safe submission limit"
                )

            logger.info("Claude request started workflow_id=%s model=%s", workflow_id, self._model)
            response = self._claude_client.analyze_image(image_bytes, mime_type)
            logger.info(
                "Claude request completed workflow_id=%s duration_seconds=%.2f "
                "input_tokens=%s output_tokens=%s model=%s",
                workflow_id,
                response.duration_seconds,
                response.input_tokens,
                response.output_tokens,
                response.model,
            )

            result = parse_analysis_response(response.text)
            logger.info("Response validation succeeded workflow_id=%s", workflow_id)

        except ClaudeRetryableError as exc:
            # SDK-level retries already exhausted, but this is not treated
            # as terminal: the workflow stays active (state unchanged,
            # pointer retained) and /retry (see handlers.py) can safely
            # re-attempt — see README.md's "Retryable versus permanent
            # failures".
            logger.warning("Analysis retryable failure workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, media_type, existing, failure_reason=type(exc).__name__)
            self._attach_reference(workflow_id)
            self._set_pending_retry(workflow_id, "analysis")
            raise
        except AnalysisError as exc:
            logger.error("Analysis failed workflow_id=%s error=%s", workflow_id, exc)
            self._record_failure(workflow_id, media_type, existing, failure_reason=type(exc).__name__)
            self._attach_reference(workflow_id)
            self._mark_workflow_failed(workflow_id, telegram_user_id)
            raise

        usage = AnalysisUsage(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        self._record_success(workflow_id, media_type, response.model, result, usage, existing)
        self._attach_reference(workflow_id)
        self._clear_pending_retry(workflow_id)
        logger.info("Analysis persisted workflow_id=%s status=COMPLETED", workflow_id)
        logger.info("Workflow updated workflow_id=%s (analysis reference attached, state unchanged)", workflow_id)

        return AnalysisOutcome(workflow_id=workflow_id, status=AnalysisStatus.COMPLETED, summary=result.summary)

    def _retrieve_media(self, s3_key: str, workflow_id: str) -> bytes:
        fd, temp_path = tempfile.mkstemp(prefix="nrc-social-agent-analysis-")
        os.close(fd)
        try:
            self._storage.download_file(s3_key, temp_path)
            with open(temp_path, "rb") as f:
                return f.read()
        except S3DownloadError as exc:
            raise MediaRetrievalError(f"failed to retrieve media for workflow_id={workflow_id}: {exc}") from exc
        finally:
            cleanup_temp_file(temp_path)

    def _record_success(
        self, workflow_id: str, media_type: str, model: str, result, usage: AnalysisUsage, existing
    ) -> None:
        kwargs = dict(
            workflow_id=workflow_id,
            model=model,
            schema_version=self._schema_version,
            prompt_version=ANALYSIS_PROMPT_VERSION,
            media_type=media_type,
            status=AnalysisStatus.COMPLETED,
            result=result,
            usage=usage,
        )
        if existing is not None:
            self._analysis_manager.supersede_failed_analysis(**kwargs)
        else:
            self._analysis_manager.create_analysis(**kwargs)

    def _record_failure(self, workflow_id: str, media_type: str, existing, *, failure_reason: str) -> None:
        kwargs = dict(
            workflow_id=workflow_id,
            model=self._model,
            schema_version=self._schema_version,
            prompt_version=ANALYSIS_PROMPT_VERSION,
            media_type=media_type,
            status=AnalysisStatus.FAILED,
            metadata={"failure_reason": failure_reason},
        )
        try:
            if existing is not None:
                self._analysis_manager.supersede_failed_analysis(**kwargs)
            else:
                self._analysis_manager.create_analysis(**kwargs)
        except AnalysisError:
            logger.error("Failed to persist analysis failure record workflow_id=%s", workflow_id, exc_info=True)

    def _attach_reference(self, workflow_id: str) -> None:
        analysis: AnalysisDocument | None = self._analysis_manager.find_existing(workflow_id)
        if analysis is None:
            return
        reference = {
            "analysis_id": analysis.analysis_id,
            "status": analysis.status.value,
            "schema_version": analysis.schema_version,
            "completed_at": analysis.updated_at,
        }
        try:
            self._workflow_manager.attach_analysis_reference(workflow_id, reference)
        except WorkflowError:
            logger.error("Failed to attach analysis reference workflow_id=%s", workflow_id, exc_info=True)

    def _mark_workflow_failed(self, workflow_id: str, telegram_user_id: int) -> None:
        try:
            self._workflow_manager.update_state(workflow_id, WorkflowState.FAILED)
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
            logger.info("Workflow state changed workflow_id=%s to=FAILED (analysis failure)", workflow_id)
        except WorkflowError:
            logger.error("Failed to mark workflow FAILED workflow_id=%s", workflow_id, exc_info=True)
            return

        clear_pointer_if_terminal(
            telegram_user_id=telegram_user_id,
            workflow_state=WorkflowState.FAILED,
            conversation_manager=self._conversation_manager,
        )

    def _set_pending_retry(self, workflow_id: str, operation: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": operation})
        except WorkflowError:
            logger.error("Failed to set pending_retry workflow_id=%s", workflow_id, exc_info=True)

    def _clear_pending_retry(self, workflow_id: str) -> None:
        try:
            self._workflow_manager.update_metadata(workflow_id, {"pending_retry": None})
        except WorkflowError:
            logger.error("Failed to clear pending_retry workflow_id=%s", workflow_id, exc_info=True)
