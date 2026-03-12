from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.services.github_tasks import Task

TELEGRAM_MSG_LIMIT = 4096

STATUS_ICONS = {
    "todo": "\u26aa",
    "in-progress": "\u23f3",
    "done": "\u2705",
    "failed": "\u274c",
}

PRIORITY_ICONS = {
    0: "\U0001f534",
    1: "\U0001f7e0",
    2: "\U0001f7e1",
    3: "\U0001f7e2",
}


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(f"([{re.escape(special)}])", r"\\\1", text)


def format_task_line(task: Task) -> str:
    icon = STATUS_ICONS.get(task.status, "\u2753")
    prio = PRIORITY_ICONS.get(task.priority, "")
    return f"{icon} {prio} \\#{task.number} \\| P{task.priority} \\| {escape_md(task.title)}"


def format_status_summary(
    todo: int, in_progress: int, done: int, failed: int, runner_paused: bool
) -> str:
    lines = [
        "*Backlog Status*",
        "",
        f"\u26aa TODO: {todo}",
        f"\u23f3 In Progress: {in_progress}",
        f"\u2705 Done: {done}",
        f"\u274c Failed: {failed}",
        "",
        f"Runner: {'\\u23f8 Paused' if runner_paused else '\\u25b6 Running'}",
    ]
    return "\n".join(lines)


def format_task_detail(task: Task) -> str:
    icon = STATUS_ICONS.get(task.status, "\u2753")
    lines = [
        f"*\\#{task.number} {escape_md(task.title)}*",
        "",
        f"Status: {icon} {escape_md(task.status)}",
        f"Priority: P{task.priority}",
    ]
    if task.depends:
        deps = ", ".join(f"\\#{d}" for d in task.depends)
        lines.append(f"Depends: {deps}")
    if task.verify_cmd:
        lines.append(f"Verify: `{escape_md(task.verify_cmd)}`")
    if task.body:
        # Strip metadata lines from body for display
        body = task.body
        body = re.sub(r"^verify:.*$", "", body, flags=re.MULTILINE)
        body = re.sub(r"^depends:.*$", "", body, flags=re.MULTILINE)
        body = body.strip()
        if body:
            lines.append("")
            lines.append(escape_md(body))
    return "\n".join(lines)


def truncate(text: str, max_len: int = TELEGRAM_MSG_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    suffix = "\n\n... (truncated)"
    return text[: max_len - len(suffix)] + suffix


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting from text."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text


def _format_table(table_lines: list[str], max_line_width: int = 46) -> list[str]:
    """Format table lines with aligned columns, fitting within max_line_width.

    For tables that are too wide (5+ columns), switches to a vertical
    key-value format instead.
    """
    # Filter out separator lines
    content_lines = [
        l for l in table_lines
        if not re.match(r"^\|[\s\-:|]+\|$", l)
    ]
    if not content_lines:
        return []

    # Parse cells and strip markdown
    rows = []
    for line in content_lines:
        cells = [_strip_markdown(c.strip()) for c in line.strip("|").split("|")]
        rows.append(cells)

    num_cols = max(len(row) for row in rows)
    headers = rows[0] if rows else []
    data_rows = rows[1:] if len(rows) > 1 else []

    # Calculate natural width per column
    col_widths = [0] * num_cols
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], len(cell))

    # Check if table fits as horizontal
    overhead = 3 * num_cols + 1
    total_width = sum(col_widths) + overhead

    if total_width > max_line_width and num_cols >= 5 and data_rows:
        # Too wide — switch to vertical key-value format
        result = ["<pre>"]
        for row in data_rows:
            first_cell = row[0] if row else "?"
            result.append(_escape_html(f"── {first_cell} ──"))
            for i in range(1, num_cols):
                header = headers[i] if i < len(headers) else f"Col{i}"
                value = row[i] if i < len(row) else ""
                result.append(_escape_html(f"  {header}: {value}"))
            result.append("")
        result.append("</pre>")
        return result

    # Horizontal table — shrink wide columns if needed
    available = max_line_width - overhead
    total_content = sum(col_widths)

    if total_content > available and available > 0:
        short_threshold = 8
        fixed_width = sum(w for w in col_widths if w <= short_threshold)
        shrinkable = [(i, w) for i, w in enumerate(col_widths) if w > short_threshold]
        remaining = available - fixed_width

        if shrinkable and remaining > 0:
            shrink_total = sum(w for _, w in shrinkable)
            for i, w in shrinkable:
                col_widths[i] = max(short_threshold, int(w * remaining / shrink_total))

    # Truncate cells to fit column widths
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols and len(cell) > col_widths[i]:
                row[i] = cell[:col_widths[i] - 1] + "…"

    # Build aligned rows
    result = ["<pre>"]
    for row in rows:
        padded = []
        for i in range(num_cols):
            cell = row[i] if i < len(row) else ""
            padded.append(cell.ljust(col_widths[i]))
        result.append(_escape_html("| " + " | ".join(padded) + " |"))
    result.append("</pre>")
    return result


def format_claude_response(text: str, as_html: bool = True) -> str:
    """Format Claude's response for Telegram HTML mode.

    Converts markdown tables to <pre> blocks and basic markdown to HTML.
    Returns (formatted_text, parse_mode) if as_html, else plain text.
    """
    if not as_html:
        return truncate(text)

    lines = text.split("\n")
    result = []
    table_lines = []
    in_table = False
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Handle existing code blocks
        if stripped.startswith("```"):
            if in_code_block:
                result.append("</pre>")
                in_code_block = False
            else:
                result.append("<pre>")
                in_code_block = True
            continue

        if in_code_block:
            result.append(_escape_html(line))
            continue

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

            # Convert basic markdown to HTML
            formatted = _escape_html(line)
            # Bold: **text** or __text__
            formatted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", formatted)
            # Italic: *text* or _text_
            formatted = re.sub(r"\*(.+?)\*", r"<i>\1</i>", formatted)
            # Inline code: `text`
            formatted = re.sub(r"`(.+?)`", r"<code>\1</code>", formatted)
            # Headers: ## text -> bold
            formatted = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", formatted)
            result.append(formatted)

    # Handle table at end of text
    if in_table and table_lines:
        result.extend(_format_table(table_lines))

    if in_code_block:
        result.append("</pre>")

    return truncate("\n".join(result))
