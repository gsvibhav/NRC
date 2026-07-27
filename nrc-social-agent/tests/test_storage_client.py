from unittest.mock import patch

from src.config import Config
from src.storage.client import build_s3_client

FAKE_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_config():
    return Config(
        telegram_bot_token=FAKE_TOKEN,
        allowed_user_ids=frozenset({1}),
        environment="development",
        log_level="INFO",
        aws_access_key_id="fake-access-key",
        aws_secret_access_key="fake-secret-key",
        aws_region="eu-west-1",
        s3_bucket_name="fake-bucket",
        s3_key_prefix="media",
        max_image_size_mb=20,
        max_video_size_mb=20,
        anthropic_api_key="fake-anthropic-key",
        anthropic_model="claude-opus-5",
        anthropic_max_tokens=4096,
        anthropic_request_timeout_seconds=60,
        anthropic_max_retries=2,
        analysis_schema_version=1,
        max_clarification_questions=4,
        max_planned_outputs=3,
        content_plan_schema_version=1,
        draft_schema_version=1,
        max_instagram_caption_length=2200,
        max_hashtags=5,
        publication_schema_version=1,
        instagram_publishing_enabled=False,
        meta_graph_api_version="v25.0",
        instagram_account_id=None,
        meta_access_token=None,
        instagram_media_url_ttl_seconds=1800,
        instagram_container_poll_interval_seconds=5.0,
        instagram_container_poll_timeout_seconds=120.0,
        dispatch_lease_seconds=300,
        meta_request_timeout_seconds=30.0,
        instagram_live_test_operator_ids=frozenset(),
    )


def test_build_s3_client_passes_explicit_credentials_and_region():
    config = _make_config()

    with patch("src.storage.client.boto3.client") as mock_client:
        build_s3_client(config)

    mock_client.assert_called_once_with(
        "s3",
        region_name="eu-west-1",
        aws_access_key_id="fake-access-key",
        aws_secret_access_key="fake-secret-key",
    )
