from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.handlers.deploy_status import (
    _check_railway,
    _check_vercel,
    _deploy_railway,
    _deploy_vercel,
    _git_merge_and_push,
)
from bot.slack_auth import is_authorized

if TYPE_CHECKING:
    from slack_bolt.async_app import AsyncApp

    from bot.config import Config

logger = logging.getLogger(__name__)


def _html_to_slack(lines: list[str]) -> str:
    """Convert HTML-formatted lines to Slack mrkdwn."""
    result = []
    in_pre = False
    for line in lines:
        if line.strip() == "<pre>":
            result.append("```")
            in_pre = True
            continue
        if line.strip() == "</pre>":
            result.append("```")
            in_pre = False
            continue
        # Convert HTML tags to Slack mrkdwn
        cleaned = line.replace("<b>", "*").replace("</b>", "*")
        cleaned = cleaned.replace("<code>", "`").replace("</code>", "`")
        # Unescape HTML entities
        cleaned = cleaned.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        result.append(cleaned)
    return "\n".join(result)


def _html_to_slack_text(html: str) -> str:
    """Convert an HTML-formatted string to Slack mrkdwn."""
    text = html.replace("<b>", "*").replace("</b>", "*")
    text = text.replace("<i>", "_").replace("</i>", "_")
    text = text.replace("<code>", "`").replace("</code>", "`")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


def register_slack_deploy_handlers(app: AsyncApp, config: Config, monitor=None):
    """Register deployment commands for Slack."""

    @app.command("/br-health")
    async def cmd_health(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        await respond(":hourglass_flowing_sand: Checking deployments...")

        vercel_lines, railway_lines = await asyncio.gather(
            _check_vercel(),
            _check_railway(),
        )

        text = _html_to_slack(vercel_lines + [""] + railway_lines)
        await respond(text)

    @app.command("/br-stable")
    async def cmd_stable(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        await respond(":hourglass_flowing_sand: Merging `dev` → `main`...")

        success, output = await _git_merge_and_push("dev", "main")

        if success:
            text = (
                ":white_check_mark: *Stable deploy triggered!*\n\n"
                "Merged `dev` → `main` and pushed.\n"
                "Railway + Vercel Stable will auto-deploy from main."
            )
        else:
            if len(output) > 2500:
                output = output[:2500] + "\n..."
            text = f":x: *Merge failed*\n\n```{output}```"

        await respond(text)

    @app.command("/br-prod")
    async def cmd_prod(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        await respond(
            ":rocket: Deploying to *Production*...\n"
            "Frontend (Vercel) + Backend + Worker (Railway)"
        )

        prod_env_id = "32825af9-f776-4cf7-94de-e076b6378f75"
        railway_lines, vercel_result = await asyncio.gather(
            _deploy_railway("Prod", prod_env_id),
            _deploy_vercel("videoscale-prod"),
        )

        lines = ["*Production Deploy*", ""]
        lines.append(f"*Vercel:* {vercel_result}")
        lines.append("")
        lines.append("*Railway:*")
        lines.extend(railway_lines)

        has_error = "error" in vercel_result or any("error" in l for l in railway_lines)
        icon = ":x:" if has_error else ":white_check_mark:"
        status = "Some errors occurred" if has_error else "All services deploying"
        lines.insert(0, f"{icon} {status}\n")

        await respond("\n".join(lines))

    @app.command("/br-monitor")
    async def cmd_monitor(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        if not monitor:
            await respond("Health monitoring not enabled.")
            return

        from bot.services.health_monitor import format_monitor_status

        statuses = monitor.get_current_status()
        html = format_monitor_status(statuses, monitor._last_check)
        await respond(_html_to_slack_text(html))
