from unittest.mock import MagicMock

from src.conversation.store import ConversationStore


def test_conversation_store_uses_conversation_prefix():
    store = ConversationStore(client=MagicMock(), bucket_name="fake-bucket")

    assert store._build_key("42") == "conversation/42.json"
