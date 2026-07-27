import json
from unittest.mock import MagicMock

import pytest

from src.ai.analysis_service import CLAUDE_MAX_IMAGE_RAW_BYTES, AnalysisOutcome, AnalysisService
from src.ai.client import ClaudeAnalysisResponse
from src.ai.errors import (
    AnalysisError,
    ClaudeAuthenticationError,
    ClaudeRateLimitError,
    ClaudeTimeoutError,
    ClaudeTransientError,
    MalformedAnalysisResponseError,
    MediaRetrievalError,
    MediaTooLargeForAnalysisError,
    UnsupportedMediaTypeForAnalysisError,
)
from src.ai.models import AnalysisDocument, AnalysisResult, AnalysisStatus, AnalysisUsage
from src.storage.s3_storage import S3DownloadError
from src.workflow.errors import WorkflowPersistenceError
from src.workflow.models import WorkflowDocument
from src.workflow.states import WorkflowState

VALID_RESPONSE = {
    "summary": "A dog on a beach.",
    "visible_subjects": ["a dog"],
    "visual_style": ["bright"],
    "dominant_themes": ["outdoors"],
    "brand_signals": [],
    "content_opportunities": [],
    "quality_observations": [],
    "safety_notes": [],
}


def _make_workflow_document(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={
            "media_type": "photo",
            "s3_key": "media/42/wf-1/original/file.jpg",
            "mime_type": "image/jpeg",
            "file_size": 1000,
        },
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def _make_analysis_document(**overrides):
    defaults = dict(
        analysis_id="an-1",
        workflow_id="wf-1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        status=AnalysisStatus.COMPLETED,
        schema_version=1,
        prompt_version=1,
        model="claude-opus-5",
        media_type="photo",
        result=AnalysisResult(**VALID_RESPONSE),
        usage=AnalysisUsage(input_tokens=10, output_tokens=5),
    )
    defaults.update(overrides)
    return AnalysisDocument(**defaults)


def _make_service(
    *,
    storage=None,
    claude_client=None,
    analysis_manager=None,
    workflow_manager=None,
    conversation_manager=None,
):
    return AnalysisService(
        model="claude-opus-5",
        schema_version=1,
        storage=storage or MagicMock(),
        claude_client=claude_client or MagicMock(),
        analysis_manager=analysis_manager or MagicMock(),
        workflow_manager=workflow_manager or MagicMock(),
        conversation_manager=conversation_manager or MagicMock(),
    )


def _writing_download_file(payload: bytes):
    def _download(key, local_path):
        with open(local_path, "wb") as f:
            f.write(payload)

    return _download


# --- success path ------------------------------------------------------


async def test_analyze_workflow_success_persists_and_attaches_reference():
    workflow_document = _make_workflow_document()
    completed = _make_analysis_document()

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"fake-image-bytes")

    claude_client = MagicMock()
    claude_client.analyze_image.return_value = ClaudeAnalysisResponse(
        text=json.dumps(VALID_RESPONSE), input_tokens=10, output_tokens=5,
        model="claude-opus-5", duration_seconds=0.5,
    )

    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, completed]
    analysis_manager.create_analysis.return_value = completed

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document

    conversation_manager = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    outcome = await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    assert outcome == AnalysisOutcome(workflow_id="wf-1", status=AnalysisStatus.COMPLETED, summary="A dog on a beach.")

    claude_client.analyze_image.assert_called_once_with(b"fake-image-bytes", "image/jpeg")

    analysis_manager.create_analysis.assert_called_once()
    _, create_kwargs = analysis_manager.create_analysis.call_args
    assert create_kwargs["status"] is AnalysisStatus.COMPLETED
    assert create_kwargs["result"] == AnalysisResult(**VALID_RESPONSE)
    assert create_kwargs["usage"] == AnalysisUsage(input_tokens=10, output_tokens=5)

    workflow_manager.attach_analysis_reference.assert_called_once()
    wf_id, reference = workflow_manager.attach_analysis_reference.call_args.args
    assert wf_id == "wf-1"
    assert reference == {
        "analysis_id": completed.analysis_id,
        "status": "COMPLETED",
        "schema_version": completed.schema_version,
        "completed_at": completed.updated_at,
    }

    # Successful analysis must not touch workflow state or the active pointer.
    workflow_manager.update_state.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()


async def test_analyze_workflow_returns_existing_completed_analysis_without_calling_claude():
    completed = _make_analysis_document()
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = _make_workflow_document()

    analysis_manager = MagicMock()
    analysis_manager.find_existing.return_value = completed

    claude_client = MagicMock()
    storage = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
    )

    outcome = await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.status is AnalysisStatus.COMPLETED
    assert outcome.summary == completed.result.summary
    claude_client.analyze_image.assert_not_called()
    storage.download_file.assert_not_called()
    analysis_manager.create_analysis.assert_not_called()
    workflow_manager.attach_analysis_reference.assert_not_called()


# --- workflow load failure -----------------------------------------------


async def test_analyze_workflow_raises_when_workflow_cannot_be_loaded():
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.side_effect = WorkflowPersistenceError("s3 down")
    analysis_manager = MagicMock()

    service = _make_service(workflow_manager=workflow_manager, analysis_manager=analysis_manager)

    with pytest.raises(AnalysisError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    analysis_manager.find_existing.assert_not_called()
    analysis_manager.create_analysis.assert_not_called()
    workflow_manager.update_state.assert_not_called()


# --- media eligibility failures -------------------------------------------


async def test_analyze_workflow_rejects_video_and_marks_workflow_failed():
    workflow_document = _make_workflow_document(media={"media_type": "video", "s3_key": "k", "mime_type": "video/mp4"})
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document

    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed

    claude_client = MagicMock()
    storage = MagicMock()
    conversation_manager = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(UnsupportedMediaTypeForAnalysisError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    claude_client.analyze_image.assert_not_called()
    storage.download_file.assert_not_called()

    analysis_manager.create_analysis.assert_called_once()
    _, create_kwargs = analysis_manager.create_analysis.call_args
    assert create_kwargs["status"] is AnalysisStatus.FAILED

    workflow_manager.attach_analysis_reference.assert_called_once()
    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_analyze_workflow_rejects_media_reported_too_large_before_download():
    workflow_document = _make_workflow_document(
        media={
            "media_type": "photo", "s3_key": "k", "mime_type": "image/jpeg",
            "file_size": CLAUDE_MAX_IMAGE_RAW_BYTES + 1,
        }
    )
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed
    storage = MagicMock()

    service = _make_service(storage=storage, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    with pytest.raises(MediaTooLargeForAnalysisError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    storage.download_file.assert_not_called()


async def test_analyze_workflow_rejects_media_too_large_after_download(monkeypatch):
    monkeypatch.setattr("src.ai.analysis_service.CLAUDE_MAX_IMAGE_RAW_BYTES", 10)
    workflow_document = _make_workflow_document(
        media={"media_type": "photo", "s3_key": "k", "mime_type": "image/jpeg", "file_size": None}
    )
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"this payload is over ten bytes")

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed
    claude_client = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
    )

    with pytest.raises(MediaTooLargeForAnalysisError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    claude_client.analyze_image.assert_not_called()


async def test_analyze_workflow_raises_media_retrieval_error_on_download_failure():
    workflow_document = _make_workflow_document()
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = S3DownloadError("boom")

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed

    service = _make_service(storage=storage, analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    with pytest.raises(MediaRetrievalError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)


# --- Claude and parsing failures -------------------------------------------


async def test_analyze_workflow_propagates_claude_transient_error_as_retryable():
    # Milestone 4B: a retryable Claude failure (SDK retries already
    # exhausted) must NOT move the workflow to FAILED or clear the active
    # pointer — it stays retryable via /retry instead.
    workflow_document = _make_workflow_document()
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"bytes")

    claude_client = MagicMock()
    claude_client.analyze_image.side_effect = ClaudeTransientError("server error")

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed
    conversation_manager = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeTransientError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()
    workflow_manager.update_metadata.assert_called_once_with("wf-1", {"pending_retry": "analysis"})


@pytest.mark.parametrize("error_cls", [ClaudeTimeoutError, ClaudeRateLimitError, ClaudeTransientError])
async def test_analyze_workflow_treats_every_retryable_claude_error_as_retryable(error_cls):
    workflow_document = _make_workflow_document()
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"bytes")
    claude_client = MagicMock()
    claude_client.analyze_image.side_effect = error_cls("transient")
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed
    conversation_manager = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(error_cls):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_not_called()
    conversation_manager.clear_active_workflow.assert_not_called()


async def test_analyze_workflow_treats_permanent_claude_error_as_terminal():
    # A non-retryable Claude failure (e.g. bad credentials) is the
    # opposite case: FAILED, pointer cleared, exactly like Milestone 4A.
    workflow_document = _make_workflow_document()
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"bytes")
    claude_client = MagicMock()
    claude_client.analyze_image.side_effect = ClaudeAuthenticationError("bad key")
    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed
    conversation_manager = MagicMock()

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager, conversation_manager=conversation_manager,
    )

    with pytest.raises(ClaudeAuthenticationError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    workflow_manager.update_state.assert_called_once_with("wf-1", WorkflowState.FAILED)
    conversation_manager.clear_active_workflow.assert_called_once_with(42)


async def test_analyze_workflow_rejects_malformed_claude_output_without_persisting_as_completed():
    workflow_document = _make_workflow_document()
    failed = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"bytes")

    claude_client = MagicMock()
    claude_client.analyze_image.return_value = ClaudeAnalysisResponse(
        text="not valid json", input_tokens=1, output_tokens=1, model="claude-opus-5", duration_seconds=0.1,
    )

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, failed]
    analysis_manager.create_analysis.return_value = failed

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
    )

    with pytest.raises(MalformedAnalysisResponseError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    # The malformed attempt must be recorded as FAILED, never as COMPLETED.
    _, create_kwargs = analysis_manager.create_analysis.call_args
    assert create_kwargs["status"] is AnalysisStatus.FAILED
    assert "result" not in create_kwargs


# --- retry-after-failure (supersede) ---------------------------------------


async def test_analyze_workflow_retries_after_a_prior_failed_analysis_via_supersede():
    workflow_document = _make_workflow_document()
    prior_failure = _make_analysis_document(status=AnalysisStatus.FAILED, result=None, usage=None)
    completed = _make_analysis_document()

    storage = MagicMock()
    storage.download_file.side_effect = _writing_download_file(b"bytes")

    claude_client = MagicMock()
    claude_client.analyze_image.return_value = ClaudeAnalysisResponse(
        text=json.dumps(VALID_RESPONSE), input_tokens=10, output_tokens=5,
        model="claude-opus-5", duration_seconds=0.2,
    )

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [prior_failure, completed]
    analysis_manager.supersede_failed_analysis.return_value = completed

    service = _make_service(
        storage=storage, claude_client=claude_client, analysis_manager=analysis_manager,
        workflow_manager=workflow_manager,
    )

    outcome = await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)

    assert outcome.status is AnalysisStatus.COMPLETED
    analysis_manager.create_analysis.assert_not_called()
    analysis_manager.supersede_failed_analysis.assert_called_once()


# --- persistence-failure resilience (best-effort, never masks real outcome) --


async def test_analyze_workflow_still_raises_original_error_if_failure_persistence_also_fails():
    workflow_document = _make_workflow_document(media={"media_type": "video", "s3_key": "k"})

    workflow_manager = MagicMock()
    workflow_manager.load_workflow.return_value = workflow_document
    analysis_manager = MagicMock()
    analysis_manager.find_existing.side_effect = [None, None]
    analysis_manager.create_analysis.side_effect = AnalysisError("persistence also failed")

    service = _make_service(analysis_manager=analysis_manager, workflow_manager=workflow_manager)

    with pytest.raises(UnsupportedMediaTypeForAnalysisError):
        await service.analyze_workflow(workflow_id="wf-1", telegram_user_id=42)
