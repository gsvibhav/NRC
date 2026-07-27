from unittest.mock import AsyncMock, MagicMock

import pytest

from src.access_control import ACCESS_DENIED_MESSAGE, is_authorized, restricted


def test_is_authorized_true_for_allowed_id():
    assert is_authorized(42, frozenset({42, 7})) is True


def test_is_authorized_false_for_disallowed_id():
    assert is_authorized(99, frozenset({42, 7})) is False


def test_is_authorized_false_for_none_user_id():
    assert is_authorized(None, frozenset({42})) is False


def _make_update_and_context(user_id, allowed_ids):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"allowed_user_ids": frozenset(allowed_ids)}
    return update, context


async def test_restricted_blocks_unauthorized_user():
    handler = AsyncMock()
    wrapped = restricted(handler)
    update, context = _make_update_and_context(999, {1, 2})

    await wrapped(update, context)

    handler.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED_MESSAGE)


async def test_restricted_allows_authorized_user():
    handler = AsyncMock()
    wrapped = restricted(handler)
    update, context = _make_update_and_context(1, {1, 2})

    await wrapped(update, context)

    handler.assert_awaited_once_with(update, context)
    update.effective_message.reply_text.assert_not_called()


async def test_restricted_blocks_when_effective_user_is_none():
    handler = AsyncMock()
    wrapped = restricted(handler)
    update, context = _make_update_and_context(1, {1})
    update.effective_user = None

    await wrapped(update, context)

    handler.assert_not_called()
    update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED_MESSAGE)
