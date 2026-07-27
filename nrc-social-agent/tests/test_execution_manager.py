from unittest.mock import MagicMock

from src.execution.errors import ExecutionNotFoundError
from src.execution.manager import ExecutionManager
from src.execution.models import ExecutionStatus
from src.execution.repository import LoadedExecution
from src.publication.models import PublicationChannel


def test_find_existing_returns_none_when_not_found():
    repository = MagicMock()
    repository.load.side_effect = ExecutionNotFoundError("none")
    manager = ExecutionManager(repository)

    assert manager.find_existing("pub-1") is None


def test_find_existing_returns_document_when_found():
    from src.execution.models import ExecutionDocument

    repository = MagicMock()
    document = ExecutionDocument(
        execution_id="exec-1", publication_id="pub-1", workflow_id="wf-1",
        channel=PublicationChannel.INSTAGRAM, status=ExecutionStatus.READY_FOR_DISPATCH,
        attempt=0, publisher="instagram", created_at="t1", updated_at="t1",
    )
    repository.load.return_value = LoadedExecution(document=document, etag='"etag-1"')
    manager = ExecutionManager(repository)

    assert manager.find_existing("pub-1") == document


def test_create_execution_persists_with_conditional_create_and_ready_for_dispatch():
    repository = MagicMock()
    manager = ExecutionManager(repository)

    document = manager.create_execution(
        publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM, publisher="instagram",
    )

    assert document.execution_id.startswith("exec_")
    assert document.publication_id == "pub-1"
    assert document.workflow_id == "wf-1"
    assert document.status is ExecutionStatus.READY_FOR_DISPATCH
    assert document.attempt == 0
    assert document.publisher == "instagram"
    repository.save.assert_called_once_with(document, expected_etag=None)


def test_create_execution_generates_unique_ids():
    repository = MagicMock()
    manager = ExecutionManager(repository)

    doc1 = manager.create_execution(
        publication_id="pub-1", workflow_id="wf-1", channel=PublicationChannel.INSTAGRAM, publisher="instagram",
    )
    doc2 = manager.create_execution(
        publication_id="pub-2", workflow_id="wf-2", channel=PublicationChannel.INSTAGRAM, publisher="instagram",
    )

    assert doc1.execution_id != doc2.execution_id
