from unittest.mock import AsyncMock, MagicMock

from src import handlers
from src.execution.errors import (
    LiveValidationConfirmationExpiredError,
    LiveValidationConfirmationNotFoundError,
    NoEligibleLiveValidationCandidateError,
)
from src.execution.live_validation import LiveValidationCandidate, LiveValidationPreview
from src.utils.dedup import SeenUpdateTracker


def _make_preview(**overrides):
    defaults = dict(
        publication_id="pub-1", execution_id="exec-1", confirmation_token="abc123def456",
        expires_at="2026-01-01T00:05:00+00:00", account_username="nrc_official", account_fingerprint="••••0000",
        placement="feed", media_mode="single_image", approved_version_number=2, media_count=1,
    )
    defaults.update(overrides)
    return LiveValidationPreview(**defaults)


def _make_outcome(**overrides):
    from src.execution.dispatch_service import DispatchOutcome

    defaults = dict(status="completed", execution_id="exec-1", publication_id="pub-1", result={"external_media_id": "m1"})
    defaults.update(overrides)
    return DispatchOutcome(**defaults)


def _make_command_context(user_id, allowed_ids, *, live_test_operator_ids=frozenset({1}), instagram_publishing_enabled=True):
    update = MagicMock()
    update.update_id = 1
    update.effective_user.id = user_id
    update.effective_message.reply_text = AsyncMock()

    config = MagicMock()
    config.instagram_publishing_enabled = instagram_publishing_enabled
    config.instagram_live_test_operator_ids = live_test_operator_ids

    live_validation_service = MagicMock()

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "config": config,
        "live_validation_service": live_validation_service,
    }
    return update, context, live_validation_service


def _make_callback_context(
    user_id, allowed_ids, *, callback_data, update_id=1, live_test_operator_ids=frozenset({1}),
):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock()
    update.update_id = update_id
    update.effective_user.id = user_id
    update.callback_query = query
    update.effective_message = query.message

    config = MagicMock()
    config.instagram_live_test_operator_ids = live_test_operator_ids

    live_validation_service = MagicMock()

    context = MagicMock()
    context.bot_data = {
        "allowed_user_ids": frozenset(allowed_ids),
        "seen_updates": SeenUpdateTracker(),
        "config": config,
        "live_validation_service": live_validation_service,
    }
    return update, context, query, live_validation_service


# --- instagram_test_publish(): authorization ---------------------------------


async def test_instagram_test_publish_denies_unauthorized_bot_user():
    update, context, _ = _make_command_context(999, {1})

    await handlers.instagram_test_publish(update, context)

    update.effective_message.reply_text.assert_awaited_once_with("Sorry, you're not authorized to use this bot.")


async def test_instagram_test_publish_denies_non_operator_even_if_allowed_user():
    update, context, service = _make_command_context(1, {1}, live_test_operator_ids=frozenset())

    await handlers.instagram_test_publish(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
    service.find_eligible_candidates.assert_not_called()


async def test_instagram_test_publish_reports_disabled_without_touching_the_service():
    update, context, service = _make_command_context(1, {1}, instagram_publishing_enabled=False)

    await handlers.instagram_test_publish(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(handlers.INSTAGRAM_PUBLISHING_DISABLED_MESSAGE)
    service.find_eligible_candidates.assert_not_called()


# --- instagram_test_publish(): eligibility outcomes --------------------------


async def test_instagram_test_publish_reports_no_eligible_candidate():
    update, context, service = _make_command_context(1, {1})
    service.find_eligible_candidates.return_value = ([], "No active, eligible publication was found.")

    await handlers.instagram_test_publish(update, context)

    (message,), _ = update.effective_message.reply_text.call_args
    assert "No active, eligible publication was found." in message


async def test_instagram_test_publish_single_candidate_shows_preview():
    update, context, service = _make_command_context(1, {1})
    candidate = LiveValidationCandidate(workflow_id="wf-1", publication_id="pub-1", execution_id="exec-1", output_id="out-1")
    service.find_eligible_candidates.return_value = ([candidate], None)
    service.build_preview.return_value = _make_preview()

    await handlers.instagram_test_publish(update, context)

    service.build_preview.assert_called_once_with(telegram_user_id=1, publication_id="pub-1")
    (message,), kwargs = update.effective_message.reply_text.call_args
    assert handlers.LIVE_VALIDATION_HEADER in message
    assert "@nrc_official" in message
    assert "••••0000" in message
    assert "179838000000000" not in message
    assert kwargs["reply_markup"] is not None


async def test_instagram_test_publish_multiple_candidates_shows_selection_list():
    update, context, service = _make_command_context(1, {1})
    candidates = [
        LiveValidationCandidate(workflow_id="wf-1", publication_id="pub-1", execution_id="exec-1", output_id="out-1"),
        LiveValidationCandidate(workflow_id="wf-2", publication_id="pub-2", execution_id="exec-2", output_id="out-2"),
    ]
    service.find_eligible_candidates.return_value = (candidates, None)

    await handlers.instagram_test_publish(update, context)

    (message,), kwargs = update.effective_message.reply_text.call_args
    assert message == handlers.LIVE_VALIDATION_MULTIPLE_CANDIDATES_MESSAGE
    buttons = kwargs["reply_markup"].inline_keyboard
    assert len(buttons) == 2
    assert buttons[0][0].callback_data == "ilv:s:pub-1"
    assert buttons[1][0].callback_data == "ilv:s:pub-2"
    service.build_preview.assert_not_called()


async def test_instagram_test_publish_preview_error_reports_safe_message():
    update, context, service = _make_command_context(1, {1})
    candidate = LiveValidationCandidate(workflow_id="wf-1", publication_id="pub-1", execution_id="exec-1", output_id="out-1")
    service.find_eligible_candidates.return_value = ([candidate], None)
    error = NoEligibleLiveValidationCandidateError("boom")
    service.build_preview.side_effect = error

    await handlers.instagram_test_publish(update, context)

    update.effective_message.reply_text.assert_awaited_once_with(error.user_message)


# --- callback: select ---------------------------------------------------------


async def test_live_validation_action_select_shows_preview():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:s:pub-1")
    service.build_preview.return_value = _make_preview()

    await handlers.instagram_live_validation_action(update, context)

    service.build_preview.assert_called_once_with(telegram_user_id=1, publication_id="pub-1")
    query.answer.assert_awaited_once()


# --- callback: authorization / dedup / malformed ------------------------------


async def test_live_validation_action_denies_unauthorized_bot_user():
    update, context, query, service = _make_callback_context(999, {1}, callback_data="ilv:c:pub-1:tok")

    await handlers.instagram_live_validation_action(update, context)

    query.message.reply_text.assert_awaited_once_with("Sorry, you're not authorized to use this bot.")


async def test_live_validation_action_denies_non_operator():
    update, context, query, service = _make_callback_context(
        1, {1}, callback_data="ilv:c:pub-1:tok", live_test_operator_ids=frozenset()
    )

    await handlers.instagram_live_validation_action(update, context)

    query.message.reply_text.assert_awaited_once_with(handlers.LIVE_VALIDATION_NOT_OPERATOR_MESSAGE)
    service.confirm.assert_not_called()


async def test_live_validation_action_skips_duplicate_update():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok", update_id=42)
    context.bot_data["seen_updates"].seen_before(42)

    await handlers.instagram_live_validation_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()
    service.confirm.assert_not_called()


async def test_live_validation_action_rejects_malformed_callback_data():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:bogus")

    await handlers.instagram_live_validation_action(update, context)

    query.answer.assert_awaited_once()
    query.message.reply_text.assert_not_called()
    service.confirm.assert_not_called()
    service.cancel.assert_not_called()


# --- callback: confirm ---------------------------------------------------------


async def test_live_validation_action_confirm_success():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    service.confirm = AsyncMock(return_value=_make_outcome())

    await handlers.instagram_live_validation_action(update, context)

    service.confirm.assert_awaited_once_with(telegram_user_id=1, publication_id="pub-1", confirmation_token="tok")
    calls = query.message.reply_text.await_args_list
    assert calls[0].args[0] == handlers.DISPATCH_STARTED_MESSAGE
    assert "Published to Instagram successfully" in calls[1].args[0]
    assert "Exactly one live publication was attempted" in calls[1].args[0]


async def test_live_validation_action_confirm_ambiguous_never_shown_as_retryable():
    from src.publisher.models import FailureCategory

    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    service.confirm = AsyncMock(
        return_value=_make_outcome(
            status="retryable_failure", result=None,
            failure={"category": FailureCategory.AMBIGUOUS_PUBLISH_OUTCOME.value, "retryable": True},
        )
    )

    await handlers.instagram_live_validation_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == handlers.LIVE_VALIDATION_AMBIGUOUS_MESSAGE
    assert "Do not retry" in calls[1].args[0]


async def test_live_validation_action_confirm_retryable_failure_mentions_no_new_confirmation_needed():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    service.confirm = AsyncMock(
        return_value=_make_outcome(
            status="retryable_failure", result=None,
            failure={"category": "TRANSIENT_PLATFORM_ERROR", "retryable": True},
        )
    )

    await handlers.instagram_live_validation_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert "/retry" in calls[1].args[0]
    assert "not required" in calls[1].args[0]


async def test_live_validation_action_confirm_stale_reports_safe_message():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    error = LiveValidationConfirmationNotFoundError("already used")
    service.confirm = AsyncMock(side_effect=error)

    await handlers.instagram_live_validation_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == error.user_message
    assert "already used" not in calls[1].args[0]  # detail never leaked


async def test_live_validation_action_confirm_expired_reports_safe_message():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    error = LiveValidationConfirmationExpiredError("expired")
    service.confirm = AsyncMock(side_effect=error)

    await handlers.instagram_live_validation_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert calls[1].args[0] == error.user_message


async def test_live_validation_action_confirm_never_reraises_unexpected_error_with_detail():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:c:pub-1:tok")
    service.confirm = AsyncMock(side_effect=RuntimeError("internal secret detail"))

    await handlers.instagram_live_validation_action(update, context)

    calls = query.message.reply_text.await_args_list
    assert "internal secret detail" not in calls[1].args[0]


# --- callback: cancel -----------------------------------------------------------


async def test_live_validation_action_cancel_calls_service_and_confirms_nothing_published():
    update, context, query, service = _make_callback_context(1, {1}, callback_data="ilv:x:pub-1:tok")

    await handlers.instagram_live_validation_action(update, context)

    service.cancel.assert_called_once_with(telegram_user_id=1, publication_id="pub-1", confirmation_token="tok")
    query.message.reply_text.assert_awaited_once_with(handlers.LIVE_VALIDATION_CANCELLED_MESSAGE)
    service.confirm.assert_not_called()


# --- callback data parsing / message formatting -------------------------------


def test_parse_live_validation_callback_data_confirm():
    assert handlers._parse_live_validation_callback_data("ilv:c:pub-1:tok") == ("c", "pub-1", "tok")


def test_parse_live_validation_callback_data_cancel():
    assert handlers._parse_live_validation_callback_data("ilv:x:pub-1:tok") == ("x", "pub-1", "tok")


def test_parse_live_validation_callback_data_select():
    assert handlers._parse_live_validation_callback_data("ilv:s:pub-1") == ("s", "pub-1", None)


def test_parse_live_validation_callback_data_rejects_dispatch_prefix():
    assert handlers._parse_live_validation_callback_data("dispatch:instagram:pub-1") is None


def test_parse_live_validation_callback_data_rejects_malformed():
    assert handlers._parse_live_validation_callback_data("ilv:c:pub-1") is None
    assert handlers._parse_live_validation_callback_data("") is None
    assert handlers._parse_live_validation_callback_data(None) is None


def test_build_live_validation_confirmation_keyboard_never_leaks_beyond_publication_id_and_token():
    markup = handlers._build_live_validation_confirmation_keyboard("pub-1", "tok-123")
    confirm_data = markup.inline_keyboard[0][0].callback_data
    cancel_data = markup.inline_keyboard[1][0].callback_data
    assert confirm_data == "ilv:c:pub-1:tok-123"
    assert cancel_data == "ilv:x:pub-1:tok-123"


def test_format_live_validation_preview_message_never_shows_secrets():
    preview = _make_preview()
    message = handlers._format_live_validation_preview_message(preview)
    assert "token" not in message.lower() or "confirmation" not in message.lower()
    assert preview.confirmation_token not in message
    assert "179838000000000" not in message


def test_callback_data_stays_under_telegram_limit():
    # Telegram's callback_data ceiling is 64 bytes.
    publication_id = "pub_" + "a" * 32
    markup = handlers._build_live_validation_confirmation_keyboard(publication_id, "0123456789ab")
    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode("utf-8")) <= 64
