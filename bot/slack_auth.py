from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import Config


def is_authorized(user_id: str, config: Config) -> bool:
    """Check if a Slack user ID is in the allowed list."""
    return user_id in config.slack_allowed_users
