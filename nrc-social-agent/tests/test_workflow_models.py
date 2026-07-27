import pytest

from src.workflow.errors import (
    InvalidWorkflowStateError,
    WorkflowDeserializationError,
    WorkflowVersionMismatchError,
)
from src.workflow.models import CURRENT_VERSION, WorkflowDocument
from src.workflow.states import WorkflowState


def _make_document(**overrides):
    defaults = dict(
        workflow_id="wf-1",
        telegram_user_id=42,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        state=WorkflowState.ANALYZING_MEDIA,
        media={"s3_key": "media/42/wf-1/original/file.jpg"},
    )
    defaults.update(overrides)
    return WorkflowDocument(**defaults)


def test_to_dict_includes_current_version_by_default():
    document = _make_document()

    data = document.to_dict()

    assert data["version"] == CURRENT_VERSION
    assert data["state"] == "ANALYZING_MEDIA"


def test_to_dict_defaults_analysis_draft_conversation_metadata():
    document = _make_document()

    data = document.to_dict()

    assert data["analysis"] is None
    assert data["draft"] is None
    assert data["conversation"] == []
    assert data["metadata"] == {}


def test_round_trip_to_dict_and_from_dict_preserves_all_fields():
    document = _make_document(conversation=[{"role": "user", "text": "hi"}], metadata={"k": "v"})

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_rejects_non_dict_input():
    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict("not a dict")


def test_from_dict_rejects_missing_version():
    data = _make_document().to_dict()
    del data["version"]

    with pytest.raises(WorkflowVersionMismatchError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_unsupported_version():
    data = _make_document().to_dict()
    data["version"] = 999

    with pytest.raises(WorkflowVersionMismatchError):
        WorkflowDocument.from_dict(data)


@pytest.mark.parametrize("missing_field", ["workflow_id", "created_at", "updated_at"])
def test_from_dict_rejects_missing_required_string_fields(missing_field):
    data = _make_document().to_dict()
    del data[missing_field]

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_missing_telegram_user_id():
    data = _make_document().to_dict()
    del data["telegram_user_id"]

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_non_integer_telegram_user_id():
    data = _make_document().to_dict()
    data["telegram_user_id"] = "42"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_unrecognized_state():
    data = _make_document().to_dict()
    data["state"] = "NOT_A_REAL_STATE"

    with pytest.raises(InvalidWorkflowStateError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_non_dict_media():
    data = _make_document().to_dict()
    data["media"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_non_list_conversation():
    data = _make_document().to_dict()
    data["conversation"] = "not a list"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_non_dict_metadata():
    data = _make_document().to_dict()
    data["metadata"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_accepts_null_analysis_and_draft():
    data = _make_document().to_dict()
    data["analysis"] = None
    data["draft"] = None

    document = WorkflowDocument.from_dict(data)

    assert document.analysis is None
    assert document.draft is None


def test_from_dict_rejects_non_dict_analysis():
    data = _make_document().to_dict()
    data["analysis"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


# --- Milestone 4B: clarification_context / pending_question --------------


def test_to_dict_defaults_clarification_context_and_pending_question_to_none():
    document = _make_document()

    data = document.to_dict()

    assert data["clarification_context"] is None
    assert data["pending_question"] is None


def test_round_trip_preserves_clarification_context_and_pending_question():
    document = _make_document(
        clarification_context={"version": 1, "brand_name": None},
        pending_question={"question_id": "q1", "text": "Which platform?"},
    )

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_reads_a_version_1_document_missing_the_new_fields():
    # A pre-Milestone-4B document never had these keys at all.
    data = _make_document().to_dict()
    data["version"] = 1
    del data["clarification_context"]
    del data["pending_question"]

    document = WorkflowDocument.from_dict(data)

    assert document.clarification_context is None
    assert document.pending_question is None
    assert document.version == 1


def test_from_dict_rejects_non_dict_clarification_context():
    data = _make_document().to_dict()
    data["clarification_context"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_from_dict_rejects_non_dict_pending_question():
    data = _make_document().to_dict()
    data["pending_question"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


def test_supports_versions_1_through_6():
    from src.workflow.models import SUPPORTED_VERSIONS

    assert SUPPORTED_VERSIONS == {1, 2, 3, 4, 5, 6}


# --- Milestone 5: content_plan ------------------------------------------


def test_to_dict_defaults_content_plan_to_none():
    document = _make_document()

    assert document.to_dict()["content_plan"] is None


def test_round_trip_preserves_content_plan():
    document = _make_document(
        content_plan={"plan_id": "p1", "status": "COMPLETED", "schema_version": 1, "output_count": 2, "primary_output_type": "instagram_reel_caption", "completed_at": "x"},
    )

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_reads_a_version_2_document_missing_content_plan():
    data = _make_document().to_dict()
    data["version"] = 2
    del data["content_plan"]

    document = WorkflowDocument.from_dict(data)

    assert document.content_plan is None
    assert document.version == 2


def test_from_dict_rejects_non_dict_content_plan():
    data = _make_document().to_dict()
    data["content_plan"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


# --- Milestone 6: generated_draft ---------------------------------------


def test_to_dict_defaults_generated_draft_to_none():
    document = _make_document()

    assert document.to_dict()["generated_draft"] is None


def test_round_trip_preserves_generated_draft():
    document = _make_document(
        generated_draft={
            "draft_id": "d1", "output_id": "out-1", "output_type": "instagram_reel_caption",
            "status": "READY_FOR_REVIEW", "schema_version": 1, "completed_at": "x",
        },
    )

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_reads_a_version_3_document_missing_generated_draft():
    data = _make_document().to_dict()
    data["version"] = 3
    del data["generated_draft"]

    document = WorkflowDocument.from_dict(data)

    assert document.generated_draft is None
    assert document.version == 3


def test_from_dict_rejects_non_dict_generated_draft():
    data = _make_document().to_dict()
    data["generated_draft"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


# --- Milestone 7: pending_edit -------------------------------------------


def test_to_dict_defaults_pending_edit_to_none():
    document = _make_document()

    assert document.to_dict()["pending_edit"] is None


def test_round_trip_preserves_pending_edit():
    document = _make_document(
        pending_edit={
            "output_id": "out-1", "operation_id": "op-1", "expected_parent_version": 1,
            "status": "AWAITING_INSTRUCTION", "instruction_turn_id": None,
            "completed_version_number": None, "requested_at": "x",
        },
    )

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_reads_a_version_4_document_missing_pending_edit():
    data = _make_document().to_dict()
    data["version"] = 4
    del data["pending_edit"]

    document = WorkflowDocument.from_dict(data)

    assert document.pending_edit is None
    assert document.version == 4


def test_from_dict_rejects_non_dict_pending_edit():
    data = _make_document().to_dict()
    data["pending_edit"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)


# --- Milestone 8: publication ---------------------------------------------


def test_to_dict_defaults_publication_to_none():
    document = _make_document()

    assert document.to_dict()["publication"] is None


def test_round_trip_preserves_publication():
    document = _make_document(
        publication={
            "publication_id": "pub-1", "output_id": "out-1", "draft_id": "draft-2", "draft_version": 2,
            "channel": "instagram", "status": "READY_FOR_PUBLISHING", "schema_version": 1, "prepared_at": "x",
        },
    )

    restored = WorkflowDocument.from_dict(document.to_dict())

    assert restored == document


def test_from_dict_reads_a_version_5_document_missing_publication():
    data = _make_document().to_dict()
    data["version"] = 5
    del data["publication"]

    document = WorkflowDocument.from_dict(data)

    assert document.publication is None
    assert document.version == 5


def test_from_dict_rejects_non_dict_publication():
    data = _make_document().to_dict()
    data["publication"] = "not a dict"

    with pytest.raises(WorkflowDeserializationError):
        WorkflowDocument.from_dict(data)
