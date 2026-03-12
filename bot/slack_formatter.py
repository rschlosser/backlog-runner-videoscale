from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.services.github_tasks import Task

SLACK_MSG_LIMIT = 3000

STATUS_ICONS = {
    "todo": ":white_circle:",
    "in-progress": ":hourglass_flowing_sand:",
    "done": ":white_check_mark:",
    "failed": ":x:",
}

PRIORITY_ICONS = {
    0: ":red_circle:",
    1: ":large_orange_circle:",
    2: ":large_yellow_circle:",
    3: ":large_green_circle:",
}


def escape_mrkdwn(text: str) -> str:
    """Escape special characters for Slack mrkdwn."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def format_task_line(task: Task) -> str:
    icon = STATUS_ICONS.get(task.status, ":question:")
    prio = PRIORITY_ICONS.get(task.priority, "")
    return f"{icon} {prio} #{task.number} | P{task.priority} | {escape_mrkdwn(task.title)}"


def format_status_summary(
    todo: int, in_progress: int, done: int, failed: int, runner_paused: bool
) -> str:
    lines = [
        "*Backlog Status*",
        "",
        f":white_circle: TODO: {todo}",
        f":hourglass_flowing_sand: In Progress: {in_progress}",
        f":white_check_mark: Done: {done}",
        f":x: Failed: {failed}",
        "",
        f"Runner: {'⏸ Paused' if runner_paused else '▶ Running'}",
    ]
    return "\n".join(lines)


def format_task_detail(task: Task) -> str:
    icon = STATUS_ICONS.get(task.status, ":question:")
    lines = [
        f"*#{task.number} {escape_mrkdwn(task.title)}*",
        "",
        f"Status: {icon} {task.status}",
        f"Priority: P{task.priority}",
    ]
    if task.depends:
        deps = ", ".join(f"#{d}" for d in task.depends)
        lines.append(f"Depends: {deps}")
    if task.verify_cmd:
        lines.append(f"Verify: `{task.verify_cmd}`")
    if task.body:
        body = task.body
        body = re.sub(r"^verify:.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^depends:.*$", "", body, flags=re.MULTILINE)
        body = body.strip()
        if body:
            lines.append("")
            lines.append(escape_mrkdwn(body))
    return "\n".join(lines)


def truncate(text: str, max_len: int = SLACK_MSG_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    suffix = "\n\n... (truncated)"
    return text[: max_len - len(suffix)] + suffix


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting from text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def _format_table(table_lines: list[str], max_line_width: int = 70) -> list[str]:
    """Format table lines with aligned columns in a code block."""
    content_lines = [
        l for l in table_lines
        if not re.match(r"^\|[\s\-:|]+\|$", l)
    ]
    if not content_lines:
        return []

    rows = []
    for line in content_lines:
        cells = [_strip_markdown(c.strip()) for c in line.strip("|").split("|")]
        rows.append(cells)

    num_cols = max(len(row) for row in rows)
    col_widths = [0] * num_cols
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], len(cell))

    overhead = 3 * num_cols + 1
    available = max_line_width - overhead
    total_width = sum(col_widths)

    if total_width > available and available > 0:
        for i in range(num_cols):
            col_widths[i] = max(3, int(col_widths[i] * available / total_width))

    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols and len(cell) > col_widths[i]:
                row[i] = cell[:col_widths[i] - 1] + "…"

    result = ["```"]
    for row in rows:
        padded = []
        for i in range(num_cols):
            cell = row[i] if i < len(row) else ""
            padded.append(cell.ljust(col_widths[i]))
        result.append("| " + " | ".join(padded) + " |")
    result.append("```")
    return result


def _convert_tables_to_code_blocks(text: str) -> str:
    """Convert markdown tables to aligned code blocks for Slack."""
    lines = text.split("\n")
    result = []
    table_lines = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        is_table_line = stripped.startswith("|") and stripped.endswith("|")

        if is_table_line:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(stripped)
        else:
            if in_table:
                result.extend(_format_table(table_lines))
                in_table = False
                table_lines = []
            result.append(line)

    if in_table and table_lines:
        result.extend(_format_table(table_lines))

    return "\n".join(result)


def format_claude_response(text: str) -> str:
    """Format Claude's response for Slack."""
    text = _convert_tables_to_code_blocks(text)
    return truncate(text)
