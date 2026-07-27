"""Environment-variable configuration for NRC Social Agent.

Per CLAUDE.md and DECISIONS.md, all configuration comes exclusively from
environment variables — never hardcoded, never read from any other source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_S3_KEY_PREFIX = "media"
DEFAULT_MAX_IMAGE_SIZE_MB = 20
DEFAULT_MAX_VIDEO_SIZE_MB = 20

# Default model per DECISIONS.md: Claude Opus 5, the current flagship
# Opus-tier model, per this project's policy of defaulting to Opus unless
# a different model is explicitly chosen (see README.md's model-selection
# rationale for the cost/latency tradeoff against Sonnet 5).
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096
DEFAULT_ANTHROPIC_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_ANTHROPIC_MAX_RETRIES = 2
DEFAULT_ANALYSIS_SCHEMA_VERSION = 1

# A safety cap on the adaptive clarification loop (Milestone 4B), not a
# target question count — most uploads should need far fewer. 4 balances
# "enough turns for genuinely ambiguous posts" against the questionnaire
# feel the whole engine is designed to avoid; see README.md.
DEFAULT_MAX_CLARIFICATION_QUESTIONS = 4

# A safety ceiling on the content plan's proposed outputs (Milestone 5),
# not a target count — "1 strong output" is an entirely valid plan. 3
# allows a primary recommendation plus up to two well-justified supporting
# outputs without drifting into "every platform, every time"; see
# README.md's "Maximum-planned-outputs policy".
DEFAULT_MAX_PLANNED_OUTPUTS = 3
DEFAULT_CONTENT_PLAN_SCHEMA_VERSION = 1

DEFAULT_DRAFT_SCHEMA_VERSION = 1

# Instagram's own caption ceiling is 2,200 characters — the target
# platform's real constraint, not Telegram's message limit (see
# README.md's "Length and platform constraints"). A Reel caption is
# typically much shorter in practice; the prompt encourages concision
# separately from this hard ceiling.
DEFAULT_MAX_INSTAGRAM_CAPTION_LENGTH = 2200

# A small, relevant set, not a spam-tag ceiling — most drafts should use
# far fewer, or none at all (hashtags are optional, never mandatory; see
# README.md's "Hashtag policy").
DEFAULT_MAX_HASHTAGS = 5

DEFAULT_PUBLICATION_SCHEMA_VERSION = 1

# Instagram Publisher Adapter and Dispatch Lifecycle (Milestone 10).
# Disabled by default — see README.md's "Feature flag / default-disabled
# behaviour": authoring, approval, publication preparation, and execution
# creation all work with zero Instagram configuration; only actually
# dispatching to Meta requires the feature explicitly enabled.
DEFAULT_INSTAGRAM_PUBLISHING_ENABLED = False

# Meta's current Graph API version as of this milestone (verified against
# developers.facebook.com's own versions page — see the completion
# report for the exact date checked). A future version bump is a config
# change, never a code change.
DEFAULT_META_GRAPH_API_VERSION = "v25.0"

# Meta's own Content Publishing docs specify only that a media *container*
# expires 24 hours after creation if unpublished — not how long the
# source media URL itself must stay reachable. 30 minutes is deliberately
# conservative: comfortably longer than container creation plus the
# bounded status-polling window below, far short of the 24-hour ceiling.
DEFAULT_INSTAGRAM_MEDIA_URL_TTL_SECONDS = 1800

# Bounded container-status polling (never indefinite) — 5-second interval,
# 2-minute overall ceiling, i.e. at most ~24 checks per dispatch attempt.
DEFAULT_INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS = 120.0

# How long a dispatch claim/lease is held before it's considered
# abandoned and eligible for expired-lease recovery.
DEFAULT_DISPATCH_LEASE_SECONDS = 300

DEFAULT_META_REQUEST_TIMEOUT_SECONDS = 30.0

MAX_ANTHROPIC_MAX_TOKENS = 128_000
SUPPORTED_ANALYSIS_SCHEMA_VERSIONS = {1}
SUPPORTED_CONTENT_PLAN_SCHEMA_VERSIONS = {1}
SUPPORTED_DRAFT_SCHEMA_VERSIONS = {1}
SUPPORTED_PUBLICATION_SCHEMA_VERSIONS = {1}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    environment: str
    log_level: str

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket_name: str
    s3_key_prefix: str
    max_image_size_mb: int
    max_video_size_mb: int

    anthropic_api_key: str
    anthropic_model: str
    anthropic_max_tokens: int
    anthropic_request_timeout_seconds: int
    anthropic_max_retries: int
    analysis_schema_version: int
    max_clarification_questions: int
    max_planned_outputs: int
    content_plan_schema_version: int
    draft_schema_version: int
    max_instagram_caption_length: int
    max_hashtags: int
    publication_schema_version: int

    instagram_publishing_enabled: bool
    meta_graph_api_version: str
    instagram_account_id: str | None
    meta_access_token: str | None
    instagram_media_url_ttl_seconds: int
    instagram_container_poll_interval_seconds: float
    instagram_container_poll_timeout_seconds: float
    dispatch_lease_seconds: int
    meta_request_timeout_seconds: float
    instagram_live_test_operator_ids: frozenset[int]

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024

    @property
    def max_video_size_bytes(self) -> int:
        return self.max_video_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Config":
        telegram_bot_token = _require("TELEGRAM_BOT_TOKEN")
        allowed_user_ids = _parse_user_ids(_require("TELEGRAM_ALLOWED_USER_IDS"))
        environment = os.environ.get("ENVIRONMENT", "development")
        log_level = os.environ.get("LOG_LEVEL", "INFO")

        aws_access_key_id = _require("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = _require("AWS_SECRET_ACCESS_KEY")
        aws_region = _require("AWS_REGION")
        s3_bucket_name = _require("S3_BUCKET_NAME")
        s3_key_prefix = os.environ.get("S3_KEY_PREFIX", DEFAULT_S3_KEY_PREFIX).strip().strip("/")
        max_image_size_mb = _parse_positive_int(
            "MAX_IMAGE_SIZE_MB", DEFAULT_MAX_IMAGE_SIZE_MB
        )
        max_video_size_mb = _parse_positive_int(
            "MAX_VIDEO_SIZE_MB", DEFAULT_MAX_VIDEO_SIZE_MB
        )

        anthropic_api_key = _require("ANTHROPIC_API_KEY")
        anthropic_model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_ANTHROPIC_MODEL
        anthropic_max_tokens = _parse_positive_int(
            "ANTHROPIC_MAX_TOKENS", DEFAULT_ANTHROPIC_MAX_TOKENS
        )
        if anthropic_max_tokens > MAX_ANTHROPIC_MAX_TOKENS:
            raise ConfigError(
                f"Invalid ANTHROPIC_MAX_TOKENS: {anthropic_max_tokens} exceeds the "
                f"maximum of {MAX_ANTHROPIC_MAX_TOKENS}"
            )
        anthropic_request_timeout_seconds = _parse_positive_int(
            "ANTHROPIC_REQUEST_TIMEOUT_SECONDS", DEFAULT_ANTHROPIC_REQUEST_TIMEOUT_SECONDS
        )
        anthropic_max_retries = _parse_non_negative_int(
            "ANTHROPIC_MAX_RETRIES", DEFAULT_ANTHROPIC_MAX_RETRIES
        )
        analysis_schema_version = _parse_positive_int(
            "ANALYSIS_SCHEMA_VERSION", DEFAULT_ANALYSIS_SCHEMA_VERSION
        )
        if analysis_schema_version not in SUPPORTED_ANALYSIS_SCHEMA_VERSIONS:
            raise ConfigError(
                f"Invalid ANALYSIS_SCHEMA_VERSION: {analysis_schema_version} is not "
                f"supported (supported: {sorted(SUPPORTED_ANALYSIS_SCHEMA_VERSIONS)})"
            )
        max_clarification_questions = _parse_positive_int(
            "MAX_CLARIFICATION_QUESTIONS", DEFAULT_MAX_CLARIFICATION_QUESTIONS
        )
        max_planned_outputs = _parse_positive_int(
            "MAX_PLANNED_OUTPUTS", DEFAULT_MAX_PLANNED_OUTPUTS
        )
        content_plan_schema_version = _parse_positive_int(
            "CONTENT_PLAN_SCHEMA_VERSION", DEFAULT_CONTENT_PLAN_SCHEMA_VERSION
        )
        if content_plan_schema_version not in SUPPORTED_CONTENT_PLAN_SCHEMA_VERSIONS:
            raise ConfigError(
                f"Invalid CONTENT_PLAN_SCHEMA_VERSION: {content_plan_schema_version} is not "
                f"supported (supported: {sorted(SUPPORTED_CONTENT_PLAN_SCHEMA_VERSIONS)})"
            )
        draft_schema_version = _parse_positive_int("DRAFT_SCHEMA_VERSION", DEFAULT_DRAFT_SCHEMA_VERSION)
        if draft_schema_version not in SUPPORTED_DRAFT_SCHEMA_VERSIONS:
            raise ConfigError(
                f"Invalid DRAFT_SCHEMA_VERSION: {draft_schema_version} is not "
                f"supported (supported: {sorted(SUPPORTED_DRAFT_SCHEMA_VERSIONS)})"
            )
        max_instagram_caption_length = _parse_positive_int(
            "MAX_INSTAGRAM_CAPTION_LENGTH", DEFAULT_MAX_INSTAGRAM_CAPTION_LENGTH
        )
        max_hashtags = _parse_non_negative_int("MAX_HASHTAGS", DEFAULT_MAX_HASHTAGS)
        publication_schema_version = _parse_positive_int(
            "PUBLICATION_SCHEMA_VERSION", DEFAULT_PUBLICATION_SCHEMA_VERSION
        )
        if publication_schema_version not in SUPPORTED_PUBLICATION_SCHEMA_VERSIONS:
            raise ConfigError(
                f"Invalid PUBLICATION_SCHEMA_VERSION: {publication_schema_version} is not "
                f"supported (supported: {sorted(SUPPORTED_PUBLICATION_SCHEMA_VERSIONS)})"
            )

        instagram_publishing_enabled = _parse_bool(
            "INSTAGRAM_PUBLISHING_ENABLED", DEFAULT_INSTAGRAM_PUBLISHING_ENABLED
        )
        meta_graph_api_version = (
            os.environ.get("META_GRAPH_API_VERSION", "").strip() or DEFAULT_META_GRAPH_API_VERSION
        )
        instagram_account_id = os.environ.get("INSTAGRAM_ACCOUNT_ID", "").strip() or None
        meta_access_token = os.environ.get("META_ACCESS_TOKEN", "").strip() or None
        if instagram_publishing_enabled:
            if not instagram_account_id:
                raise ConfigError(
                    "Missing required environment variable: INSTAGRAM_ACCOUNT_ID "
                    "(required because INSTAGRAM_PUBLISHING_ENABLED=true)"
                )
            if not meta_access_token:
                raise ConfigError(
                    "Missing required environment variable: META_ACCESS_TOKEN "
                    "(required because INSTAGRAM_PUBLISHING_ENABLED=true)"
                )

        instagram_media_url_ttl_seconds = _parse_positive_int(
            "INSTAGRAM_MEDIA_URL_TTL_SECONDS", DEFAULT_INSTAGRAM_MEDIA_URL_TTL_SECONDS
        )
        instagram_container_poll_interval_seconds = _parse_positive_float(
            "INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS", DEFAULT_INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS
        )
        instagram_container_poll_timeout_seconds = _parse_positive_float(
            "INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS", DEFAULT_INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS
        )
        if instagram_container_poll_timeout_seconds <= instagram_container_poll_interval_seconds:
            raise ConfigError(
                "Invalid INSTAGRAM_CONTAINER_POLL_TIMEOUT_SECONDS: must be greater than "
                "INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS"
            )
        dispatch_lease_seconds = _parse_positive_int("DISPATCH_LEASE_SECONDS", DEFAULT_DISPATCH_LEASE_SECONDS)
        meta_request_timeout_seconds = _parse_positive_float(
            "META_REQUEST_TIMEOUT_SECONDS", DEFAULT_META_REQUEST_TIMEOUT_SECONDS
        )
        # Milestone 11B: deliberately NEVER required at startup, even when
        # INSTAGRAM_PUBLISHING_ENABLED=true — ordinary dispatch (the
        # existing "Publish to Instagram" button) must keep working with
        # zero live-test operators configured. An empty set simply means
        # nobody is currently authorized to use /instagram_test_publish;
        # the command itself enforces that at call time, not startup.
        instagram_live_test_operator_ids = _parse_optional_user_ids("INSTAGRAM_LIVE_TEST_OPERATOR_IDS")

        return cls(
            telegram_bot_token=telegram_bot_token,
            allowed_user_ids=allowed_user_ids,
            environment=environment,
            log_level=log_level,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_region=aws_region,
            s3_bucket_name=s3_bucket_name,
            s3_key_prefix=s3_key_prefix,
            max_image_size_mb=max_image_size_mb,
            max_video_size_mb=max_video_size_mb,
            anthropic_api_key=anthropic_api_key,
            anthropic_model=anthropic_model,
            anthropic_max_tokens=anthropic_max_tokens,
            anthropic_request_timeout_seconds=anthropic_request_timeout_seconds,
            anthropic_max_retries=anthropic_max_retries,
            analysis_schema_version=analysis_schema_version,
            max_clarification_questions=max_clarification_questions,
            max_planned_outputs=max_planned_outputs,
            content_plan_schema_version=content_plan_schema_version,
            draft_schema_version=draft_schema_version,
            max_instagram_caption_length=max_instagram_caption_length,
            max_hashtags=max_hashtags,
            publication_schema_version=publication_schema_version,
            instagram_publishing_enabled=instagram_publishing_enabled,
            meta_graph_api_version=meta_graph_api_version,
            instagram_account_id=instagram_account_id,
            meta_access_token=meta_access_token,
            instagram_media_url_ttl_seconds=instagram_media_url_ttl_seconds,
            instagram_container_poll_interval_seconds=instagram_container_poll_interval_seconds,
            instagram_container_poll_timeout_seconds=instagram_container_poll_timeout_seconds,
            dispatch_lease_seconds=dispatch_lease_seconds,
            meta_request_timeout_seconds=meta_request_timeout_seconds,
            instagram_live_test_operator_ids=instagram_live_test_operator_ids,
        )


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _parse_user_ids(raw: str) -> frozenset[int]:
    user_ids: list[int] = []
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            user_ids.append(int(candidate))
        except ValueError as exc:
            raise ConfigError(
                f"Invalid entry in TELEGRAM_ALLOWED_USER_IDS: {candidate!r} is not an integer"
            ) from exc

    if not user_ids:
        raise ConfigError("TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID")

    return frozenset(user_ids)


def _parse_optional_user_ids(name: str) -> frozenset[int]:
    """Like _parse_user_ids(), but never required and never fails on an
    empty/unset value — an empty result is a valid, safe default (nobody
    is authorized) rather than a startup error."""

    raw = os.environ.get(name, "").strip()
    if not raw:
        return frozenset()

    user_ids: list[int] = []
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            user_ids.append(int(candidate))
        except ValueError as exc:
            raise ConfigError(f"Invalid entry in {name}: {candidate!r} is not an integer") from exc

    return frozenset(user_ids)


def _parse_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid {name}: {raw!r} is not an integer") from exc

    if value <= 0:
        raise ConfigError(f"Invalid {name}: {raw!r} must be a positive integer")

    return value


def _parse_non_negative_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid {name}: {raw!r} is not an integer") from exc

    if value < 0:
        raise ConfigError(f"Invalid {name}: {raw!r} must not be negative")

    return value


def _parse_positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"Invalid {name}: {raw!r} is not a number") from exc

    if value <= 0:
        raise ConfigError(f"Invalid {name}: {raw!r} must be a positive number")

    return value


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    raise ConfigError(f"Invalid {name}: {raw!r} is not a recognized boolean (use true/false)")
