from __future__ import annotations

import asyncio
import logging
import signal
import sys

from dotenv import load_dotenv

from bot.config import Config
from bot.services.claude_bridge import ClaudeBridge
from bot.services.github_tasks import GitHubTaskManager
from bot.services.health_monitor import HealthMonitor
from bot.services.runner import TaskRunner
from bot.services.session_store import SessionStore

logger = logging.getLogger(__name__)


async def main():
    # Load .env from project root
    load_dotenv()

    config = Config.from_env()
    errors = config.validate()
    if errors:
        for err in errors:
            print(f"Config error: {err}", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    config.sessions_dir.mkdir(parents=True, exist_ok=True)

    # Initialize shared services
    github = GitHubTaskManager(config.github_repo, config.github_token)
    claude = ClaudeBridge(config.project_dir)
    sessions = SessionStore(config.sessions_dir)

    # Telegram app (optional)
    telegram_app = None
    if config.telegram_bot_token:
        from telegram.ext import Application

        from bot.handlers.backlog import register_backlog_handlers
        from bot.handlers.chat import register_chat_handlers

        telegram_app = Application.builder().token(config.telegram_bot_token).build()
        logger.info("Telegram bot configured")

    # Slack app (optional)
    slack_app = None
    slack_handler = None
    if config.slack_bot_token:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        from slack_bolt.async_app import AsyncApp

        from bot.handlers.slack_backlog import register_slack_backlog_handlers
        from bot.handlers.slack_chat import register_slack_chat_handlers

        slack_app = AsyncApp(token=config.slack_bot_token)
        slack_handler = AsyncSocketModeHandler(slack_app, config.slack_app_token)
        logger.info("Slack bot configured")

    # Notification callback — sends to both Telegram and Slack
    async def notify_all(message: str):
        if telegram_app:
            for user_id in config.telegram_allowed_users:
                try:
                    await telegram_app.bot.send_message(chat_id=user_id, text=message)
                except Exception as e:
                    logger.error(f"Failed to notify Telegram user {user_id}: {e}")
        if slack_app and config.slack_channel:
            try:
                await slack_app.client.chat_postMessage(
                    channel=config.slack_channel, text=message
                )
            except Exception as e:
                logger.error(f"Failed to notify Slack channel: {e}")

    runner = TaskRunner(config, github, claude, notify=notify_all)

    # Health monitor
    monitor = None
    if config.monitor_enabled:
        monitor = HealthMonitor(notify=notify_all, interval=config.monitor_interval)

    # Register Telegram handlers
    if telegram_app:
        from bot.handlers.deploy_status import register_deploy_handlers

        register_backlog_handlers(telegram_app, config, github, runner)
        register_chat_handlers(telegram_app, config, claude, sessions, github=github)
        register_deploy_handlers(telegram_app, config, monitor=monitor)

    # Register Slack handlers
    if slack_app:
        from bot.handlers.slack_deploy_status import register_slack_deploy_handlers

        register_slack_backlog_handlers(slack_app, config, github, runner)
        register_slack_chat_handlers(slack_app, config, claude, sessions, github=github)
        register_slack_deploy_handlers(slack_app, config, monitor=monitor)

    # Ensure GitHub labels exist
    try:
        await github._ensure_labels()
        logger.info("GitHub labels verified")
    except Exception as e:
        logger.warning(f"Could not ensure GitHub labels: {e}")

    # Start everything
    interfaces = []
    if telegram_app:
        interfaces.append("Telegram")
    if slack_app:
        interfaces.append("Slack")
    logger.info(f"Starting bot ({', '.join(interfaces)}) + runner (project: {config.project_dir})")

    # Start Telegram
    if telegram_app:
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)

    # Start Slack
    slack_task = None
    if slack_handler:
        try:
            slack_task = asyncio.create_task(slack_handler.start_async())
            # Give it a moment to connect
            await asyncio.sleep(2)
            logger.info("Slack bot connected")
        except Exception as e:
            logger.error(f"Slack failed to start: {e}")
            logger.info("Continuing without Slack...")
            slack_handler = None
            slack_task = None

    # Start task runner
    runner_task = asyncio.create_task(runner.run_loop())

    # Start health monitor
    monitor_task = None
    if monitor:
        monitor_task = asyncio.create_task(monitor.run_loop())
        logger.info(f"Health monitor started (interval={config.monitor_interval}s)")

    # Wait for shutdown signal
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await stop_event.wait()

    # Graceful shutdown
    logger.info("Shutting down...")
    runner.stop()
    runner_task.cancel()

    if monitor:
        monitor.stop()
    if monitor_task:
        monitor_task.cancel()

    if slack_task:
        slack_task.cancel()
        try:
            await slack_handler.close_async()
        except Exception:
            pass

    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

    await github.close()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
