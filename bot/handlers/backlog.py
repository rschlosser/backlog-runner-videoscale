from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.auth import restricted
from bot.formatter import (
    escape_md,
    format_status_summary,
    format_task_detail,
    format_task_line,
    truncate,
)

if TYPE_CHECKING:
    from bot.config import Config
    from bot.services.github_tasks import GitHubTaskManager
    from bot.services.runner import TaskRunner

logger = logging.getLogger(__name__)


def register_backlog_handlers(app, config: Config, github: GitHubTaskManager, runner: TaskRunner):
    """Register all backlog-related command handlers."""
    auth = restricted(config)

    @auth
    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            msg += f"\n\nCurrent: \\#{current.number} {escape_md(current.title)}"

        await update.message.reply_text(msg, parse_mode="MarkdownV2")

    @auth
    async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
        status_filter = context.args[0] if context.args else None
        valid = {"todo", "in-progress", "done", "failed"}

        if status_filter and status_filter not in valid:
            await update.message.reply_text(
                f"Valid filters: {', '.join(valid)}"
            )
            return

        tasks = await github.get_tasks(status_filter)
        if not tasks:
            await update.message.reply_text("No tasks found.")
            return

        tasks.sort(key=lambda t: (t.priority, t.number))
        lines = [format_task_line(t) for t in tasks[:20]]
        msg = "\n".join(lines)
        if len(tasks) > 20:
            msg += f"\n\n\\.\\.\\.and {len(tasks) - 20} more"

        await update.message.reply_text(msg, parse_mode="MarkdownV2")

    @auth
    async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.replace("/add", "", 1).strip()
        if not text:
            await update.message.reply_text(
                "Usage: /add P1 Task title\nOptional description on next lines"
            )
            return

        # Parse priority and title from first line
        lines = text.split("\n", 1)
        first_line = lines[0].strip()
        description = lines[1].strip() if len(lines) > 1 else ""

        prio_match = re.match(r"^P([0-3])\s+(.+)$", first_line)
        if not prio_match:
            await update.message.reply_text(
                "Format: /add P1 Task title\n(Priority must be P0-P3)"
            )
            return

        priority = int(prio_match.group(1))
        title = prio_match.group(2).strip()

        task = await github.create_task(title, description, priority)
        await update.message.reply_text(
            f"\u2705 Created #{task.number}: {task.title} (P{task.priority})"
        )

    @auth
    async def cmd_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /detail 42  or  /detail #42")
            return

        issue_num = context.args[0].lstrip("#")
        try:
            task = await github.get_task_detail(int(issue_num))
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        msg = format_task_detail(task)
        await update.message.reply_text(truncate(msg), parse_mode="MarkdownV2")

    @auth
    async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /logs 42  or  /logs #42")
            return

        issue_num = int(context.args[0].lstrip("#"))
        try:
            # Fetch last comment from the issue (execution logs are posted as comments)
            import httpx

            resp = await github.client.get(
                f"{github.base_url}/issues/{issue_num}/comments",
                params={"per_page": 5, "direction": "desc"},
            )
            resp.raise_for_status()
            comments = resp.json()

            if not comments:
                await update.message.reply_text("No execution logs found.")
                return

            last_comment = comments[-1]["body"]
            await update.message.reply_text(truncate(last_comment))

        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    @auth
    async def cmd_retry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /retry 42  or  /retry #42")
            return

        issue_num = int(context.args[0].lstrip("#"))
        try:
            task = await github.get_task_detail(issue_num)
            if task.status != "failed":
                await update.message.reply_text(
                    f"Task #{issue_num} is {task.status}, not failed."
                )
                return

            await github.update_status(issue_num, "todo")
            await update.message.reply_text(
                f"\u267b\ufe0f Reset #{issue_num} to TODO. Runner will pick it up."
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    @auth
    async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
        runner.pause()
        await update.message.reply_text("\u23f8 Runner paused.")

    @auth
    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
        runner.resume()
        await update.message.reply_text("\u25b6\ufe0f Runner resumed.")

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("detail", cmd_detail))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
