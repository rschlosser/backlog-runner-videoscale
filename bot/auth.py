from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Callable

from telegram import Update
from telegram.ext import ContextTypes

if TYPE_CHECKING:
    from bot.config import Config

logger = logging.getLogger(__name__)


def restricted(config: Config) -> Callable:
    """Decorator that restricts handler access to allowed Telegram user IDs."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(
            update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
        ):
            user_id = update.effective_user.id if update.effective_user else None
            if user_id not in config.telegram_allowed_users:
                logger.warning(f"Unauthorized access attempt from user {user_id}")
                if update.message:
                    await update.message.reply_text("Not authorized.")
                return
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator
