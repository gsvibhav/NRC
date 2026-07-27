from unittest.mock import MagicMock

from src.workflow.draft_store import DraftStore
from src.workflow.state_store import WorkflowStateStore


def test_workflow_state_store_uses_state_prefix():
    store = WorkflowStateStore(client=MagicMock(), bucket_name="fake-bucket")

    assert store._build_key("wf-1") == "state/wf-1.json"


def test_draft_store_uses_drafts_prefix():
    store = DraftStore(client=MagicMock(), bucket_name="fake-bucket")

    assert store._build_key("wf-1") == "drafts/wf-1.json"
