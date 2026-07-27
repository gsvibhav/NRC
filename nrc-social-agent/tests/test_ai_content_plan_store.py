from unittest.mock import MagicMock

from src.ai.content_plan_store import ContentPlanStore


def test_content_plan_store_uses_plans_prefix():
    store = ContentPlanStore(client=MagicMock(), bucket_name="fake-bucket")

    assert store._build_key("wf-1") == "plans/wf-1.json"
