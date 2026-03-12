from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from bot.slack_auth import is_authorized
from bot.slack_formatter import (
    escape_mrkdwn,
    format_status_summary,
    format_task_detail,
    format_task_line,
    truncate,
)

if TYPE_CHECKING:
    from slack_bolt.async_app import AsyncApp

    from bot.config import Config
    from bot.services.github_tasks import GitHubTaskManager
    from bot.services.runner import TaskRunner

logger = logging.getLogger(__name__)


def register_slack_backlog_handlers(
    app: AsyncApp, config: Config, github: GitHubTaskManager, runner: TaskRunner
):
    """Register all backlog-related Slack command handlers."""

    @app.command("/br-status")
    async def cmd_status(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        tasks = await github.get_tasks()
        counts = {"todo": 0, "in-progress": 0, "done": 0, "failed": 0}
        for task in tasks:
            if task.status in counts:
                counts[task.status] += 1

        msg = format_status_summary(
            counts["todo"],
            counts["in-progress"],
            counts["done"],
            counts["failed"],
            runner.is_paused,
        )

        current = runner.current_task
        if current:
            msg += f"\n\nCurrent: #{current.number} {escape_mrkdwn(current.title)}"

        await respond(msg)

    @app.command("/br-list")
    async def cmd_list(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        text = command.get("text", "").strip()
        status_filter = text if text else None
        valid = {"todo", "in-progress", "done", "failed"}

        if status_filter and status_filter not in valid:
            await respond(f"Valid filters: {', '.join(valid)}")
            return

        tasks = await github.get_tasks(status_filter)
        if not tasks:
            await respond("No tasks found.")
            return

        tasks.sort(key=lambda t: (t.priority, t.number))
        lines = [format_task_line(t) for t in tasks[:20]]
        msg = "\n".join(lines)
        if len(tasks) > 20:
            msg += f"\n\n...and {len(tasks) - 20} more"

        await respond(msg)

    @app.command("/br-add")
    async def cmd_add(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        text = command.get("text", "").strip()
        if not text:
            await respond("Usage: /br-add P1 Task title\nOptional description on next lines")
            return

        lines = text.split("\n", 1)
        first_line = lines[0].strip()
        description = lines[1].strip() if len(lines) > 1 else ""

        prio_match = re.match(r"^P([0-3])\s+(.+)$", first_line)
        if not prio_match:
            await respond("Format: /br-add P1 Task title\n(Priority must be P0-P3)")
            return

        priority = int(prio_match.group(1))
        title = prio_match.group(2).strip()

        task = await github.create_task(title, description, priority)
        await respond(f":white_check_mark: Created #{task.number}: {task.title} (P{task.priority})")

    @app.command("/br-detail")
    async def cmd_detail(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        text = command.get("text", "").strip().lstrip("#")
        if not text:
            await respond("Usage: /br-detail 42")
            return

        try:
            task = await github.get_task_detail(int(text))
        except Exception as e:
            await respond(f"Error: {e}")
            return

        msg = format_task_detail(task)
        await respond(truncate(msg))

    @app.command("/br-logs")
    async def cmd_logs(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        text = command.get("text", "").strip().lstrip("#")
        if not text:
            await respond("Usage: /br-logs 42")
            return

        try:
            issue_num = int(text)
            resp = await github.client.get(
                f"{github.base_url}/issues/{issue_num}/comments",
                params={"per_page": 5, "direction": "desc"},
            )
            resp.raise_for_status()
            comments = resp.json()

            if not comments:
                await respond("No execution logs found.")
                return

            last_comment = comments[-1]["body"]
            await respond(truncate(last_comment))

        except Exception as e:
            await respond(f"Error: {e}")

    @app.command("/br-retry")
    async def cmd_retry(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        text = command.get("text", "").strip().lstrip("#")
        if not text:
            await respond("Usage: /br-retry 42")
            return

        try:
            issue_num = int(text)
            task = await github.get_task_detail(issue_num)
            if task.status != "failed":
                await respond(f"Task #{issue_num} is {task.status}, not failed.")
                return

            await github.update_status(issue_num, "todo")
            await respond(f":recycle: Reset #{issue_num} to TODO. Runner will pick it up.")
        except Exception as e:
            await respond(f"Error: {e}")

    @app.command("/br-pause")
    async def cmd_pause(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        runner.pause()
        await respond(":double_vertical_bar: Runner paused.")

    @app.command("/br-resume")
    async def cmd_resume(ack, respond, command):
        await ack()
        if not is_authorized(command["user_id"], config):
            await respond("Not authorized.")
            return

        runner.resume()
        await respond(":arrow_forward: Runner resumed.")
