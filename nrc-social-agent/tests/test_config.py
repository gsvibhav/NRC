import pytest

from src.config import Config, ConfigError


def _set_all_required_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "fake-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret-key")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("S3_BUCKET_NAME", "fake-bucket")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")


def test_from_env_requires_bot_token(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        Config.from_env()


def test_from_env_requires_allowed_user_ids(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_IDS"):
        Config.from_env()


def test_from_env_rejects_blank_allowed_user_ids(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "   ")

    with pytest.raises(ConfigError, match="TELEGRAM_ALLOWED_USER_IDS"):
        Config.from_env()


def test_from_env_rejects_non_integer_user_id(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123,abc")

    with pytest.raises(ConfigError, match="abc"):
        Config.from_env()


def test_from_env_parses_multiple_ids_with_whitespace(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", " 111, 222 ,333")

    config = Config.from_env()

    assert config.allowed_user_ids == frozenset({111, 222, 333})


def test_from_env_defaults_environment_and_log_level(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    config = Config.from_env()

    assert config.environment == "development"
    assert config.log_level == "INFO"


def test_from_env_respects_explicit_environment_and_log_level(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    config = Config.from_env()

    assert config.environment == "production"
    assert config.log_level == "DEBUG"


@pytest.mark.parametrize(
    "missing_var",
    ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "S3_BUCKET_NAME"],
)
def test_from_env_requires_each_aws_variable(monkeypatch, missing_var):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv(missing_var, raising=False)

    with pytest.raises(ConfigError, match=missing_var):
        Config.from_env()


def test_from_env_defaults_s3_key_prefix_and_size_limits(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("S3_KEY_PREFIX", raising=False)
    monkeypatch.delenv("MAX_IMAGE_SIZE_MB", raising=False)
    monkeypatch.delenv("MAX_VIDEO_SIZE_MB", raising=False)

    config = Config.from_env()

    assert config.s3_key_prefix == "media"
    assert config.max_image_size_mb == 20
    assert config.max_video_size_mb == 20
    assert config.max_image_size_bytes == 20 * 1024 * 1024
    assert config.max_video_size_bytes == 20 * 1024 * 1024


def test_from_env_respects_explicit_s3_key_prefix_and_size_limits(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("S3_KEY_PREFIX", "/custom-prefix/")
    monkeypatch.setenv("MAX_IMAGE_SIZE_MB", "5")
    monkeypatch.setenv("MAX_VIDEO_SIZE_MB", "50")

    config = Config.from_env()

    assert config.s3_key_prefix == "custom-prefix"
    assert config.max_image_size_mb == 5
    assert config.max_video_size_mb == 50


def test_from_env_rejects_non_integer_max_image_size(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("MAX_IMAGE_SIZE_MB", "not-a-number")

    with pytest.raises(ConfigError, match="MAX_IMAGE_SIZE_MB"):
        Config.from_env()


def test_from_env_rejects_non_positive_max_video_size(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("MAX_VIDEO_SIZE_MB", "0")

    with pytest.raises(ConfigError, match="MAX_VIDEO_SIZE_MB"):
        Config.from_env()


# --- Anthropic / analysis configuration --------------------------------


def test_from_env_requires_anthropic_api_key(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        Config.from_env()


def test_from_env_defaults_anthropic_settings(monkeypatch):
    _set_all_required_env(monkeypatch)
    for var in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_MAX_TOKENS",
        "ANTHROPIC_REQUEST_TIMEOUT_SECONDS",
        "ANTHROPIC_MAX_RETRIES",
        "ANALYSIS_SCHEMA_VERSION",
    ):
        monkeypatch.delenv(var, raising=False)

    config = Config.from_env()

    assert config.anthropic_model == "claude-opus-5"
    assert config.anthropic_max_tokens == 4096
    assert config.anthropic_request_timeout_seconds == 60
    assert config.anthropic_max_retries == 2
    assert config.analysis_schema_version == 1


def test_from_env_respects_explicit_anthropic_settings(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "2048")
    monkeypatch.setenv("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("ANTHROPIC_MAX_RETRIES", "0")
    monkeypatch.setenv("ANALYSIS_SCHEMA_VERSION", "1")

    config = Config.from_env()

    assert config.anthropic_model == "claude-sonnet-5"
    assert config.anthropic_max_tokens == 2048
    assert config.anthropic_request_timeout_seconds == 30
    assert config.anthropic_max_retries == 0
    assert config.analysis_schema_version == 1


def test_from_env_allows_zero_anthropic_max_retries(monkeypatch):
    # ANTHROPIC_MAX_RETRIES=0 (disable SDK-level retries entirely) must be
    # valid, unlike the other positive-int-only settings.
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MAX_RETRIES", "0")

    config = Config.from_env()

    assert config.anthropic_max_retries == 0


def test_from_env_rejects_negative_anthropic_max_retries(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MAX_RETRIES", "-1")

    with pytest.raises(ConfigError, match="ANTHROPIC_MAX_RETRIES"):
        Config.from_env()


def test_from_env_rejects_non_positive_anthropic_max_tokens(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "0")

    with pytest.raises(ConfigError, match="ANTHROPIC_MAX_TOKENS"):
        Config.from_env()


def test_from_env_rejects_anthropic_max_tokens_above_ceiling(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "999999")

    with pytest.raises(ConfigError, match="ANTHROPIC_MAX_TOKENS"):
        Config.from_env()


def test_from_env_rejects_non_positive_anthropic_timeout(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigError, match="ANTHROPIC_REQUEST_TIMEOUT_SECONDS"):
        Config.from_env()


def test_from_env_rejects_unsupported_analysis_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANALYSIS_SCHEMA_VERSION", "99")

    with pytest.raises(ConfigError, match="ANALYSIS_SCHEMA_VERSION"):
        Config.from_env()


def test_from_env_rejects_non_integer_analysis_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("ANALYSIS_SCHEMA_VERSION", "not-a-number")

    with pytest.raises(ConfigError, match="ANALYSIS_SCHEMA_VERSION"):
        Config.from_env()


# --- Primary Draft Generation configuration (Milestone 6) ---------------


def test_from_env_defaults_draft_generation_settings(monkeypatch):
    _set_all_required_env(monkeypatch)
    for var in ("DRAFT_SCHEMA_VERSION", "MAX_INSTAGRAM_CAPTION_LENGTH", "MAX_HASHTAGS"):
        monkeypatch.delenv(var, raising=False)

    config = Config.from_env()

    assert config.draft_schema_version == 1
    assert config.max_instagram_caption_length == 2200
    assert config.max_hashtags == 5


def test_from_env_respects_explicit_draft_generation_settings(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("DRAFT_SCHEMA_VERSION", "1")
    monkeypatch.setenv("MAX_INSTAGRAM_CAPTION_LENGTH", "1500")
    monkeypatch.setenv("MAX_HASHTAGS", "3")

    config = Config.from_env()

    assert config.draft_schema_version == 1
    assert config.max_instagram_caption_length == 1500
    assert config.max_hashtags == 3


def test_from_env_rejects_unsupported_draft_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("DRAFT_SCHEMA_VERSION", "99")

    with pytest.raises(ConfigError, match="DRAFT_SCHEMA_VERSION"):
        Config.from_env()


def test_from_env_rejects_non_positive_max_instagram_caption_length(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("MAX_INSTAGRAM_CAPTION_LENGTH", "0")

    with pytest.raises(ConfigError, match="MAX_INSTAGRAM_CAPTION_LENGTH"):
        Config.from_env()


def test_from_env_allows_zero_max_hashtags(monkeypatch):
    # A restrained, premium draft may reasonably use zero hashtags — 0
    # must be valid, unlike the other positive-int-only settings.
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("MAX_HASHTAGS", "0")

    config = Config.from_env()

    assert config.max_hashtags == 0


def test_from_env_rejects_negative_max_hashtags(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("MAX_HASHTAGS", "-1")

    with pytest.raises(ConfigError, match="MAX_HASHTAGS"):
        Config.from_env()


# --- Publication Preparation Layer configuration (Milestone 8) -----------


def test_from_env_defaults_publication_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("PUBLICATION_SCHEMA_VERSION", raising=False)

    config = Config.from_env()

    assert config.publication_schema_version == 1


def test_from_env_respects_explicit_publication_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("PUBLICATION_SCHEMA_VERSION", "1")

    config = Config.from_env()

    assert config.publication_schema_version == 1


def test_from_env_rejects_unsupported_publication_schema_version(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("PUBLICATION_SCHEMA_VERSION", "99")

    with pytest.raises(ConfigError, match="PUBLICATION_SCHEMA_VERSION"):
        Config.from_env()


# --- Instagram Publisher Adapter and Dispatch Lifecycle (Milestone 10) ---


def test_from_env_defaults_instagram_publishing_disabled(monkeypatch):
    _set_all_required_env(monkeypatch)
    for var in ("INSTAGRAM_PUBLISHING_ENABLED", "INSTAGRAM_ACCOUNT_ID", "META_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    config = Config.from_env()

    assert config.instagram_publishing_enabled is False
    assert config.instagram_account_id is None
    assert config.meta_access_token is None
    assert config.meta_graph_api_version == "v25.0"


def test_from_env_authoring_startup_succeeds_with_instagram_publishing_disabled(monkeypatch):
    # Explicitly verifies that missing Instagram credentials never break
    # unrelated authoring startup when the feature is disabled.
    _set_all_required_env(monkeypatch)
    monkeypatch.delenv("INSTAGRAM_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("INSTAGRAM_PUBLISHING_ENABLED", "false")

    config = Config.from_env()

    assert config.instagram_publishing_enabled is False


def test_from_env_rejects_missing_instagram_account_id_when_enabled(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_PUBLISHING_ENABLED", "true")
    monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
    monkeypatch.delenv("INSTAGRAM_ACCOUNT_ID", raising=False)

    with pytest.raises(ConfigError, match="INSTAGRAM_ACCOUNT_ID"):
        Config.from_env()


def test_from_env_rejects_missing_meta_access_token_when_enabled(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_PUBLISHING_ENABLED", "true")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17841400000000000")
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="META_ACCESS_TOKEN"):
        Config.from_env()


def test_from_env_accepts_instagram_publishing_enabled_with_credentials(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_PUBLISHING_ENABLED", "true")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17841400000000000")
    monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")

    config = Config.from_env()

    assert config.instagram_publishing_enabled is True
    assert config.instagram_account_id == "17841400000000000"
    assert config.meta_access_token == "fake-token"


def test_from_env_rejects_invalid_instagram_publishing_enabled_value(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_PUBLISHING_ENABLED", "maybe")

    with pytest.raises(ConfigError, match="INSTAGRAM_PUBLISHING_ENABLED"):
        Config.from_env()


def test_from_env_rejects_poll_timeout_not_greater_than_interval(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS", "10")
    monkeypatch.setenv("INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS", "10")

    with pytest.raises(ConfigError, match="INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS"):
        Config.from_env()


def test_from_env_rejects_non_positive_media_url_ttl(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("INSTAGRAM_MEDIA_URL_TTL_SECONDS", "0")

    with pytest.raises(ConfigError, match="INSTAGRAM_MEDIA_URL_TTL_SECONDS"):
        Config.from_env()


def test_from_env_rejects_non_positive_dispatch_lease_seconds(monkeypatch):
    _set_all_required_env(monkeypatch)
    monkeypatch.setenv("DISPATCH_LEASE_SECONDS", "-5")

    with pytest.raises(ConfigError, match="DISPATCH_LEASE_SECONDS"):
        Config.from_env()
