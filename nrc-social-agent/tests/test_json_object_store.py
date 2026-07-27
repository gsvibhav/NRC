import io
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.storage.json_object_store import (
    JsonObjectStore,
    ObjectConcurrentModificationError,
    ObjectDeserializationError,
    ObjectNotFoundError,
    ObjectSerializationError,
    ObjectStoreError,
)


def _make_store(client=None):
    return JsonObjectStore(client=client or MagicMock(), bucket_name="fake-bucket", key_prefix="state")


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "Operation")


def test_build_key_uses_prefix_and_json_suffix():
    store = _make_store()

    assert store._build_key("wf-1") == "state/wf-1.json"


def test_read_returns_data_and_etag():
    client = MagicMock()
    client.get_object.return_value = {
        "Body": io.BytesIO(b'{"a": 1}'),
        "ETag": '"etag-123"',
    }
    store = _make_store(client)

    data, etag = store.read("wf-1")

    assert data == {"a": 1}
    assert etag == '"etag-123"'
    client.get_object.assert_called_once_with(Bucket="fake-bucket", Key="state/wf-1.json")


def test_read_raises_not_found_for_missing_key():
    client = MagicMock()
    client.get_object.side_effect = _client_error("NoSuchKey")
    store = _make_store(client)

    with pytest.raises(ObjectNotFoundError):
        store.read("missing")


def test_read_raises_deserialization_error_for_invalid_json():
    client = MagicMock()
    client.get_object.return_value = {"Body": io.BytesIO(b"not json"), "ETag": '"e"'}
    store = _make_store(client)

    with pytest.raises(ObjectDeserializationError):
        store.read("wf-1")


def test_read_raises_generic_store_error_for_other_failures():
    client = MagicMock()
    client.get_object.side_effect = _client_error("AccessDenied")
    store = _make_store(client)

    with pytest.raises(ObjectStoreError):
        store.read("wf-1")


def test_write_uses_if_none_match_when_creating():
    client = MagicMock()
    client.put_object.return_value = {"ETag": '"new-etag"'}
    store = _make_store(client)

    etag = store.write("wf-1", {"a": 1}, expected_etag=None)

    assert etag == '"new-etag"'
    _, kwargs = client.put_object.call_args
    assert kwargs["IfNoneMatch"] == "*"
    assert "IfMatch" not in kwargs
    assert kwargs["Bucket"] == "fake-bucket"
    assert kwargs["Key"] == "state/wf-1.json"
    assert kwargs["ContentType"] == "application/json"


def test_write_uses_if_match_when_updating():
    client = MagicMock()
    client.put_object.return_value = {"ETag": '"updated-etag"'}
    store = _make_store(client)

    store.write("wf-1", {"a": 1}, expected_etag='"old-etag"')

    _, kwargs = client.put_object.call_args
    assert kwargs["IfMatch"] == '"old-etag"'
    assert "IfNoneMatch" not in kwargs


def test_write_raises_concurrent_modification_on_precondition_failed():
    client = MagicMock()
    client.put_object.side_effect = _client_error("PreconditionFailed")
    store = _make_store(client)

    with pytest.raises(ObjectConcurrentModificationError):
        store.write("wf-1", {"a": 1}, expected_etag=None)


def test_write_raises_generic_store_error_for_other_failures():
    client = MagicMock()
    client.put_object.side_effect = _client_error("InternalError")
    store = _make_store(client)

    with pytest.raises(ObjectStoreError):
        store.write("wf-1", {"a": 1}, expected_etag='"etag"')


def test_write_raises_serialization_error_for_non_json_data():
    store = _make_store()

    with pytest.raises(ObjectSerializationError):
        store.write("wf-1", {"bad": object()}, expected_etag=None)


def test_exists_returns_true_when_head_object_succeeds():
    client = MagicMock()
    store = _make_store(client)

    assert store.exists("wf-1") is True
    client.head_object.assert_called_once_with(Bucket="fake-bucket", Key="state/wf-1.json")


def test_exists_returns_false_for_404():
    client = MagicMock()
    client.head_object.side_effect = _client_error("404")
    store = _make_store(client)

    assert store.exists("wf-1") is False


def test_exists_raises_for_other_failures():
    client = MagicMock()
    client.head_object.side_effect = _client_error("AccessDenied")
    store = _make_store(client)

    with pytest.raises(ObjectStoreError):
        store.exists("wf-1")
