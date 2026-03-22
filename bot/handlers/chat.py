from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.auth import restricted
from bot.formatter import format_claude_response, truncate

if TYPE_CHECKING:
    from bot.config import Config
    from bot.services.claude_bridge import ClaudeBridge
    from bot.services.github_tasks import GitHubTaskManager
    from bot.services.session_store import SessionStore

logger = logging.getLogger(__name__)

# Heuristic: messages that likely request changes
CHANGE_KEYWORDS = {
    "add", "fix", "change", "create", "update", "remove", "delete",
    "modify", "refactor", "implement", "write", "move", "rename",
    "install", "upgrade", "deploy", "migrate",
}


def _looks_like_change_request(text: str) -> bool:
    """Heuristic to detect if a message requests code changes."""
    words = set(text.lower().split())
    return bool(words & CHANGE_KEYWORDS)


def register_chat_handlers(
    app, config: Config, claude: ClaudeBridge, sessions: SessionStore,
    github: GitHubTaskManager | None = None,
):
    """Register chat-mode command and message handlers."""
    auth = restricted(config)

    @auth
    async def cmd_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)

        if sessions.has_active_session(user_id):
            await update.message.reply_text(
                "You already have an active chat session. Use /endchat to end it first."
            )
            return

        session = sessions.create(user_id)
        session.state = "idle"
        sessions.update(session)

        await update.message.reply_text(
            "Chat mode started. Send me messages and I'll forward them to Claude Code.\n"
            "Changes will require your approval before execution.\n"
            "Use /endchat to end the session."
        )

    @auth
    async def cmd_endchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        session = sessions.get(user_id)

        if not session:
            await update.message.reply_text("No active chat session.")
            return

        sessions.remove(user_id)
        await update.message.reply_text("Chat session ended.")

    @auth
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-text messages in chat mode."""
        user_id = str(update.effective_user.id)
        session = sessions.get(user_id)

        if not session:
            return  # Not in chat mode, ignore

        if session.state in ("awaiting_response", "executing"):
            await update.message.reply_text("Please wait, Claude is still working...")
            return

        if session.state == "awaiting_approval":
            await update.message.reply_text(
                "Please approve or reject the pending plan first."
            )
            return

        text = update.message.text
        is_change = _looks_like_change_request(text)

        # Update session state
        session.state = "awaiting_response"
        sessions.update(session)

        status_msg = await update.message.reply_text("\u23f3 Thinking...")

        async def on_progress(preview: str):
            """Update Telegram message with Claude's live output."""
            truncated = preview[-3500:] if len(preview) > 3500 else preview
            try:
                await status_msg.edit_text(f"\u23f3 Working...\n\n{truncated}")
            except Exception:
                pass

        try:
            result = await claude.send_message(
                text,
                plan_only=is_change,
                conversation_id=session.conversation_id or None,
                on_progress=on_progress,
            )

            session.conversation_id = result.conversation_id

            if is_change and result.success:
                # Show plan with approve/reject buttons
                session.state = "awaiting_approval"
                session.pending_plan = result.output
                sessions.update(session)

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("\u2705 Approve", callback_data="approve"),
                        InlineKeyboardButton("\u274c Reject", callback_data="reject"),
                    ]
                ])

                response = format_claude_response(result.output)
                await update.message.reply_text(
                    f"<b>Proposed Plan:</b>\n\n{response}",
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                # Direct response (read-only query or failed)
                session.state = "idle"
                sessions.update(session)

                response = format_claude_response(result.output)
                await update.message.reply_text(response, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Chat message failed: {e}", exc_info=True)
            session.state = "idle"
            sessions.update(session)
            await update.message.reply_text(f"Error: {e}")

    async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle approve/reject button callbacks."""
        query = update.callback_query
        await query.answer()

        raw_user_id = query.from_user.id
        if raw_user_id not in config.telegram_allowed_users:
            return

        user_id = str(raw_user_id)
        session = sessions.get(user_id)
        if not session or session.state != "awaiting_approval":
            await query.edit_message_reply_markup(reply_markup=None)
            return

        action = query.data

        if action == "reject":
            session.state = "idle"
            session.pending_plan = ""
            sessions.update(session)
            await query.edit_message_text("Plan rejected.")
            return

        if action == "approve":
            session.state = "executing"
            sessions.update(session)
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("\u23f3 Executing plan...")

            try:
                result = await claude.execute_plan(session.conversation_id)
                session.conversation_id = result.conversation_id or session.conversation_id
                session.state = "idle"
                session.pending_plan = ""
                sessions.update(session)

                status = "\u2705 Done" if result.success else "\u274c Failed"
                response = format_claude_response(result.output)
                await query.message.reply_text(f"{status}\n\n{response}", parse_mode="HTML")

            except Exception as e:
                logger.error(f"Plan execution failed: {e}", exc_info=True)
                session.state = "idle"
                sessions.update(session)
                await query.message.reply_text(f"Execution error: {e}")

    @auth
    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages — in chat mode, forward to Claude; otherwise create a GitHub issue."""
        user_id = str(update.effective_user.id)
        session = sessions.get(user_id)

        # Download the photo either way
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_data = await file.download_as_bytearray()
        caption = (update.message.caption or "").strip()

        logger.info(f"Received Telegram photo ({len(image_data)} bytes), chat_mode={session is not None}")

        if session:
            # Chat mode: send photo + caption to Claude as context
            if session.state in ("awaiting_response", "executing"):
                await update.message.reply_text("Please wait, Claude is still working...")
                return

            # Save image locally so Claude can read it with its Read tool
            ext = "jpg"
            filename = f"{uuid.uuid4().hex[:12]}.{ext}"
            from pathlib import Path
            local_path = Path(claude.project_dir) / ".tmp-screenshots" / filename
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(bytes(image_data))
            logger.info(f"Saved screenshot locally: {local_path}")

            # Build message for Claude with the local file path
            message = caption if caption else "Here is a screenshot for context."
            message += f"\n\nI've attached a screenshot. Read it from: {local_path}"

            is_change = _looks_like_change_request(message)
            session.state = "awaiting_response"
            sessions.update(session)

            status_msg = await update.message.reply_text("\u23f3 Thinking...")

            async def on_progress(preview: str):
                truncated = preview[-3500:] if len(preview) > 3500 else preview
                try:
                    await status_msg.edit_text(f"\u23f3 Working...\n\n{truncated}")
                except Exception:
                    pass

            try:
                result = await claude.send_message(
                    message,
                    plan_only=is_change,
                    conversation_id=session.conversation_id or None,
                    on_progress=on_progress,
                )
                session.conversation_id = result.conversation_id

                if is_change and result.success:
                    session.state = "awaiting_approval"
                    session.pending_plan = result.output
                    sessions.update(session)

                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("\u2705 Approve", callback_data="approve"),
                            InlineKeyboardButton("\u274c Reject", callback_data="reject"),
                        ]
                    ])
                    response = format_claude_response(result.output)
                    await update.message.reply_text(
                        f"<b>Proposed Plan:</b>\n\n{response}",
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                else:
                    session.state = "idle"
                    sessions.update(session)
                    response = format_claude_response(result.output)
                    await update.message.reply_text(response, parse_mode="HTML")

            except Exception as e:
                logger.error(f"Chat photo message failed: {e}", exc_info=True)
                session.state = "idle"
                sessions.update(session)
                await update.message.reply_text(f"Error: {e}")
            return

        # Not in chat mode: create a GitHub issue
        if not github:
            await update.message.reply_text("GitHub integration not configured.")
            return

        ext = "jpg"
        filename = f"{uuid.uuid4().hex[:12]}.{ext}"
        path = f".github/screenshots/{filename}"
        encoded = base64.b64encode(bytes(image_data)).decode("ascii")

        try:
            resp = await github.client.put(
                f"{github.base_url}/contents/{path}",
                json={
                    "message": f"chore: add screenshot {filename}",
                    "content": encoded,
                },
            )
            resp.raise_for_status()
            image_url = f"https://github.com/{github.repo}/blob/main/{path}?raw=true"
            logger.info(f"Uploaded screenshot {filename} -> {image_url}")
        except Exception as e:
            logger.error(f"Failed to upload image to GitHub: {e}")
            await update.message.reply_text(f"Failed to upload image: {e}")
            return

        title = caption if caption else "Bug report from Telegram"

        priority = 2
        prio_match = re.match(r"^P([0-3])\s+(.+)$", title)
        if prio_match:
            priority = int(prio_match.group(1))
            title = prio_match.group(2)

        user = update.effective_user
        user_name = user.full_name if user else "Unknown"

        body_parts = [
            "## Screenshots",
            f"![screenshot]({image_url})",
            "",
            f"_Reported via Telegram by {user_name}_",
        ]
        body = "\n".join(body_parts)

        task = await github.create_task(title, body, priority)
        issue_url = f"https://github.com/{github.repo}/issues/{task.number}"
        await update.message.reply_text(
            f"\u2705 Created issue #{task.number}: {task.title} (P{task.priority})\n{issue_url}"
        )

    app.add_handler(CommandHandler("chat", cmd_chat))
    app.add_handler(CommandHandler("endchat", cmd_endchat))
    app.add_handler(CallbackQueryHandler(handle_approval, pattern="^(approve|reject)$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Message handler must be added last — it catches all text not matching commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
