from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.storage.s3_storage import S3ObjectCollisionError, S3Storage, S3UploadError


def _make_storage(client=None):
    return S3Storage(client=client or MagicMock(), bucket_name="fake-bucket", key_prefix="media")


def test_build_object_key_matches_the_documented_layout():
    storage = _make_storage()

    key = storage.build_object_key(
        telegram_user_id=123, workflow_id="abc123", filename="file.jpg"
    )

    assert key == "media/123/abc123/original/file.jpg"


def test_build_object_key_uses_configured_prefix():
    storage = S3Storage(client=MagicMock(), bucket_name="fake-bucket", key_prefix="custom")

    key = storage.build_object_key(1, "wf1", "file.mp4")

    assert key == "custom/1/wf1/original/file.mp4"


def test_upload_file_calls_put_object_with_conditional_write(tmp_path):
    local_file = tmp_path / "upload.jpg"
    local_file.write_bytes(b"fake-image-bytes")

    client = MagicMock()
    storage = _make_storage(client)

    storage.upload_file(
        str(local_file),
        "media/1/wf1/original/upload.jpg",
        content_type="image/jpeg",
        metadata={"telegram-user-id": "1"},
    )

    client.put_object.assert_called_once()
    _, kwargs = client.put_object.call_args
    assert kwargs["Bucket"] == "fake-bucket"
    assert kwargs["Key"] == "media/1/wf1/original/upload.jpg"
    assert kwargs["ContentType"] == "image/jpeg"
    assert kwargs["Metadata"] == {"telegram-user-id": "1"}
    assert kwargs["IfNoneMatch"] == "*"


def test_upload_file_raises_collision_error_on_precondition_failed(tmp_path):
    local_file = tmp_path / "upload.jpg"
    local_file.write_bytes(b"data")

    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed", "Message": "precondition failed"}},
        "PutObject",
    )
    storage = _make_storage(client)

    with pytest.raises(S3ObjectCollisionError):
        storage.upload_file(str(local_file), "key", content_type="image/jpeg", metadata={})


def test_upload_file_raises_upload_error_on_other_client_errors(tmp_path):
    local_file = tmp_path / "upload.jpg"
    local_file.write_bytes(b"data")

    client = MagicMock()
    client.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "PutObject",
    )
    storage = _make_storage(client)

    with pytest.raises(S3UploadError):
        storage.upload_file(str(local_file), "key", content_type="image/jpeg", metadata={})


def test_upload_file_raises_upload_error_when_local_file_missing():
    storage = _make_storage()

    with pytest.raises(S3UploadError):
        storage.upload_file(
            "/tmp/nrc-social-agent-does-not-exist.jpg",
            "key",
            content_type="image/jpeg",
            metadata={},
        )
