from unittest.mock import MagicMock

from src.publication.manager import PublicationManager
from src.publication.models import PublicationChannel, PublicationPackage, PublicationStatus
from src.publication.repository import LoadedPublication


def _make_document(**overrides) -> PublicationPackage:
    defaults = dict(
        publication_id="pub-1", workflow_id="wf-1", plan_id="plan-1", output_id="out-1",
        output_type="instagram_reel_caption", channel=PublicationChannel.INSTAGRAM,
        status=PublicationStatus.READY_FOR_PUBLISHING, schema_version=1,
        created_at="t1", updated_at="t1",
    )
    defaults.update(overrides)
    return PublicationPackage(**defaults)


# --- find_existing ---------------------------------------------------------


def test_find_existing_returns_none_when_not_found():
    from src.publication.errors import PublicationNotFoundError

    repository = MagicMock()
    repository.load.side_effect = PublicationNotFoundError("none")
    manager = PublicationManager(repository)

    assert manager.find_existing("wf-1", "out-1") is None


def test_find_existing_returns_document_when_found():
    repository = MagicMock()
    document = _make_document()
    repository.load.return_value = LoadedPublication(document=document, etag='"etag-1"')
    manager = PublicationManager(repository)

    assert manager.find_existing("wf-1", "out-1") == document


# --- create_publication -----------------------------------------------


def test_create_publication_persists_with_conditional_create():
    repository = MagicMock()
    manager = PublicationManager(repository)

    document = manager.create_publication(
        workflow_id="wf-1", plan_id="plan-1", output_id="out-1", output_type="instagram_reel_caption",
        channel=PublicationChannel.INSTAGRAM, schema_version=1, status=PublicationStatus.READY_FOR_PUBLISHING,
        content={"caption": "Hi"},
    )

    assert document.publication_id.startswith("pub_")
    assert document.workflow_id == "wf-1"
    assert document.status is PublicationStatus.READY_FOR_PUBLISHING
    repository.save.assert_called_once_with(document, expected_etag=None)


def test_create_publication_generates_unique_ids():
    repository = MagicMock()
    manager = PublicationManager(repository)

    doc1 = manager.create_publication(
        workflow_id="wf-1", plan_id="plan-1", output_id="out-1", output_type="instagram_reel_caption",
        channel=PublicationChannel.INSTAGRAM, schema_version=1, status=PublicationStatus.READY_FOR_PUBLISHING,
    )
    doc2 = manager.create_publication(
        workflow_id="wf-2", plan_id="plan-2", output_id="out-2", output_type="instagram_reel_caption",
        channel=PublicationChannel.INSTAGRAM, schema_version=1, status=PublicationStatus.READY_FOR_PUBLISHING,
    )

    assert doc1.publication_id != doc2.publication_id


# --- supersede_failed_publication ---------------------------------------


def test_supersede_failed_publication_uses_loaded_etag():
    repository = MagicMock()
    existing = _make_document(status=PublicationStatus.FAILED)
    repository.load.return_value = LoadedPublication(document=existing, etag='"etag-1"')
    manager = PublicationManager(repository)

    updated = manager.supersede_failed_publication(
        workflow_id="wf-1", output_id="out-1", channel=PublicationChannel.INSTAGRAM, schema_version=1,
        status=PublicationStatus.READY_FOR_PUBLISHING, content={"caption": "Hi"},
    )

    assert updated.status is PublicationStatus.READY_FOR_PUBLISHING
    assert updated.publication_id == existing.publication_id
    repository.save.assert_called_once_with(updated, expected_etag='"etag-1"')


def test_supersede_failed_publication_preserves_identity_fields():
    repository = MagicMock()
    existing = _make_document(status=PublicationStatus.FAILED)
    repository.load.return_value = LoadedPublication(document=existing, etag='"etag-1"')
    manager = PublicationManager(repository)

    updated = manager.supersede_failed_publication(
        workflow_id="wf-1", output_id="out-1", channel=PublicationChannel.INSTAGRAM, schema_version=1,
        status=PublicationStatus.READY_FOR_PUBLISHING,
    )

    assert updated.workflow_id == existing.workflow_id
    assert updated.output_id == existing.output_id
    assert updated.plan_id == existing.plan_id
