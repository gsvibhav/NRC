from unittest.mock import MagicMock

from src.ai.analysis_store import AnalysisStore


def test_analysis_store_uses_analysis_prefix():
    store = AnalysisStore(client=MagicMock(), bucket_name="fake-bucket")

    assert store._build_key("wf-1") == "analysis/wf-1.json"
