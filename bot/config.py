from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # GitHub
    github_token: str = ""
    github_repo: str = ""  # "owner/repo"

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_users: list[int] = field(default_factory=list)

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_allowed_users: list[str] = field(default_factory=list)
    slack_channel: str = ""

    # Runner
    project_dir: Path = Path("/project")
    runner_interval: int = 30
    max_retries: int = 3
    max_verify_retries: int = 3
    default_verify_cmd: str = ""

    # Logging
    log_level: str = "info"
    log_dir: Path = Path("logs")

    # Sessions
    sessions_dir: Path = Path(".sessions")

    # Health Monitor
    monitor_interval: int = 300  # seconds between checks
    monitor_enabled: bool = True

    # Worker
    worker_id: str = "worker-1"

    @classmethod
    def from_env(cls) -> Config:
        allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        user_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]

        slack_allowed = os.getenv("SLACK_ALLOWED_USERS", "")
        slack_user_ids = [uid.strip() for uid in slack_allowed.split(",") if uid.strip()]

        return cls(
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_repo=os.getenv("GITHUB_REPO", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_allowed_users=user_ids,
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
            slack_allowed_users=slack_user_ids,
            slack_channel=os.getenv("SLACK_CHANNEL", ""),
            project_dir=Path(os.getenv("PROJECT_DIR", "/project")),
            runner_interval=int(os.getenv("RUNNER_INTERVAL", "30")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            max_verify_retries=int(os.getenv("MAX_VERIFY_RETRIES", "3")),
            default_verify_cmd=os.getenv("DEFAULT_VERIFY_CMD", ""),
            log_level=os.getenv("LOG_LEVEL", "info"),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            sessions_dir=Path(os.getenv("SESSIONS_DIR", ".sessions")),
            monitor_interval=int(os.getenv("MONITOR_INTERVAL", "300")),
            monitor_enabled=os.getenv("MONITOR_ENABLED", "true").lower() == "true",
            worker_id=os.getenv("WORKER_ID", "worker-1"),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.github_token:
            errors.append("GITHUB_TOKEN is required")
        if not self.github_repo:
            errors.append("GITHUB_REPO is required")
        if not self.telegram_bot_token and not self.slack_bot_token:
            errors.append("At least one of TELEGRAM_BOT_TOKEN or SLACK_BOT_TOKEN is required")
        if self.telegram_bot_token and not self.telegram_allowed_users:
            errors.append("TELEGRAM_ALLOWED_USERS is required when using Telegram")
        if self.slack_bot_token and not self.slack_app_token:
            errors.append("SLACK_APP_TOKEN is required when using Slack")
        if self.slack_bot_token and not self.slack_allowed_users:
            errors.append("SLACK_ALLOWED_USERS is required when using Slack")
        return errors
