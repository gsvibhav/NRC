"""Access control for NRC Social Agent's Telegram bot.

Per docs/WORKFLOW.md and PRODUCT.md, this is a private tool for a small,
trusted set of internal NRC staff — not a public bot. Every handler must be
wrapped with @restricted so only IDs listed in TELEGRAM_ALLOWED_USER_IDS can
interact with it.
"""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ACCESS_DENIED_MESSAGE = "Sorry, you're not authorized to use this bot."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def is_authorized(user_id: int | None, allowed_user_ids: frozenset[int]) -> bool:
    return user_id is not None and user_id in allowed_user_ids


def is_live_test_operator(user_id: int | None, live_test_operator_ids: frozenset[int]) -> bool:
    """Milestone 11B: a second, narrower gate on top of the general
    Telegram allowlist — every ordinary bot user is not automatically
    trusted to perform the first controlled Instagram live-validation
    publish. An empty `live_test_operator_ids` (the default — see
    src/config.py) means nobody is authorized yet, never "everybody"."""

    return user_id is not None and user_id in live_test_operator_ids


def restricted(handler: Handler) -> Handler:
    """Only invoke `handler` for users in TELEGRAM_ALLOWED_USER_IDS.

    Unauthorized users receive a short, generic denial — never internal
    details such as configuration or stack traces.
    """

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        allowed_user_ids = context.bot_data["allowed_user_ids"]
        user = update.effective_user
        user_id = user.id if user else None

        if not is_authorized(user_id, allowed_user_ids):
            logger.warning("Rejected update from unauthorized user_id=%s", user_id)
            if update.effective_message is not None:
                await update.effective_message.reply_text(ACCESS_DENIED_MESSAGE)
            return

        await handler(update, context)

    return wrapper
