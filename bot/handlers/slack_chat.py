from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bot.handlers.slack_images import handle_image_message
from bot.slack_auth import is_authorized
from bot.slack_formatter import format_claude_response

if TYPE_CHECKING:
    from slack_bolt.async_app import AsyncApp

    from bot.config import Config
    from bot.services.claude_bridge import ClaudeBridge
    from bot.services.github_tasks import GitHubTaskManager
    from bot.services.session_store import SessionStore

logger = logging.getLogger(__name__)

CHANGE_KEYWORDS = {
    "add", "fix", "change", "create", "update", "remove", "delete",
    "modify", "refactor", "implement", "write", "move", "rename",
    "install", "upgrade", "deploy", "migrate",
}


def _looks_like_change_request(text: str) -> bool:
    words = set(text.lower().split())
    return bool(words & CHANGE_KEYWORDS)


def register_slack_chat_handlers(
    app: AsyncApp, config: Config, claude: ClaudeBridge, sessions: SessionStore,
    github: GitHubTaskManager | None = None,
):
    """Register chat-mode Slack handlers."""

    @app.command("/br-chat")
    async def cmd_chat(ack, respond, command):
        await ack()
        user_id = command["user_id"]
        if not is_authorized(user_id, config):
            await respond("Not authorized.")
            return

        if sessions.has_active_session(user_id):
            await respond("You already have an active chat session. Use /br-endchat to end it first.")
            return

        session = sessions.create(user_id)
        session.state = "idle"
        sessions.update(session)

        await respond(
            "Chat mode started. Send messages in this channel and I'll forward them to Claude Code.\n"
            "Changes will require your approval before execution.\n"
            "Use /br-endchat to end the session."
        )

    @app.command("/br-endchat")
    async def cmd_endchat(ack, respond, command):
        await ack()
        user_id = command["user_id"]
        if not is_authorized(user_id, config):
            await respond("Not authorized.")
            return

        session = sessions.get(user_id)
        if not session:
            await respond("No active chat session.")
            return

        sessions.remove(user_id)
        await respond("Chat session ended.")

    # Debounce state: collect file_shared events and batch them
    _pending_files: dict[str, dict] = {}  # key: "user_id:channel_id"
    _pending_tasks: dict[str, asyncio.Task] = {}
    _seen_file_ids: dict[str, float] = {}  # file_id -> timestamp

    @app.event("file_shared")
    async def handle_file_shared(event, say, client):
        """Handle file_shared events — debounce and batch multiple files into one issue."""
        logger.debug(f"file_shared event: {event}")
        if not github:
            return

        file_id = event.get("file_id")
        user_id = event.get("user_id") or event.get("user")
        channel_id = event.get("channel_id")
        event_ts = float(event.get("event_ts", 0))

        if not file_id or not user_id or not is_authorized(user_id, config):
            return

        # Skip old events from before this process started (Slack retries)
        import time
        boot_time = time.time() - 30  # ignore events older than 30s before now
        if event_ts and event_ts < boot_time:
            logger.info(f"Skipping old file_shared event for {file_id} (event_ts={event_ts})")
            return

        # Deduplicate retries for the same file
        if file_id in _seen_file_ids:
            logger.info(f"Skipping duplicate file_shared for {file_id}")
            return
        _seen_file_ids[file_id] = event_ts

        key = f"{user_id}:{channel_id}"

        if key not in _pending_files:
            _pending_files[key] = {
                "user_id": user_id,
                "channel_id": channel_id,
                "file_ids": [],
            }
        _pending_files[key]["file_ids"].append(file_id)
        logger.info(f"file_shared: added {file_id} to batch {key} (now {len(_pending_files[key]['file_ids'])} files)")

        # Only start a batch task if one isn't already running for this key.
        # The task will wait until no new files arrive for 3 seconds.
        if key not in _pending_tasks or _pending_tasks[key].done():
            async def process_batch(batch_key):
                # Wait until the file count stabilises (no new files for 3s)
                while True:
                    count = len(_pending_files.get(batch_key, {}).get("file_ids", []))
                    await asyncio.sleep(3)
                    new_count = len(_pending_files.get(batch_key, {}).get("file_ids", []))
                    if new_count == count:
                        break
                    logger.info(f"Debounce: files grew {count}->{new_count}, waiting again")

                pending = _pending_files.pop(batch_key, None)
                _pending_tasks.pop(batch_key, None)
                if not pending:
                    return

                logger.info(f"Processing batch for {batch_key}: {len(pending['file_ids'])} files")

                try:
                    file_datas = []
                    for fid in pending["file_ids"]:
                        try:
                            file_info = await client.files_info(file=fid)
                            fd = file_info.get("file", {})
                            if fd.get("mimetype", "").startswith("image/"):
                                file_datas.append(fd)
                        except Exception as e:
                            logger.error(f"Failed to fetch file {fid}: {e}")

                    if not file_datas:
                        return

                    # Get the message text from the first file's shares
                    msg_text = ""
                    shares = file_datas[0].get("shares", {})
                    for share_type in ("public", "private"):
                        for ch_id, msgs in shares.get(share_type, {}).items():
                            if msgs:
                                ts = msgs[0].get("ts")
                                if ts:
                                    try:
                                        result = await client.conversations_history(
                                            channel=ch_id, latest=ts, inclusive=True, limit=1
                                        )
                                        messages = result.get("messages", [])
                                        if messages:
                                            msg_text = messages[0].get("text", "")
                                    except Exception:
                                        pass
                                break
                        if msg_text:
                            break

                    synthetic_event = {
                        "user": pending["user_id"],
                        "text": msg_text,
                        "files": file_datas,
                        "channel": pending["channel_id"],
                    }

                    async def say_in_channel(**kwargs):
                        kwargs["channel"] = pending["channel_id"]
                        return await client.chat_postMessage(**kwargs)

                    await handle_image_message(synthetic_event, say_in_channel, config, github)

                except Exception as e:
                    logger.error(f"file_shared batch handler error: {e}", exc_info=True)

            _pending_tasks[key] = asyncio.create_task(process_batch(key))

    @app.event("message")
    async def handle_message(event, say, client):
        """Handle free-text messages — chat mode forwards to Claude. Images handled via file_shared."""
        # Skip file_share subtypes — images are handled by the file_shared event handler
        if event.get("subtype") == "file_share":
            return

        user_id = event.get("user", "")
        if not user_id or not is_authorized(user_id, config):
            return

        # Ignore bot messages, edits, etc.
        if event.get("subtype"):
            return

        session = sessions.get(user_id)
        if not session:
            return  # Not in chat mode

        if session.state in ("awaiting_response", "executing"):
            await say(text="Please wait, Claude is still working...", thread_ts=event.get("ts"))
            return

        if session.state == "awaiting_approval":
            await say(
                text="Please approve or reject the pending plan first.",
                thread_ts=event.get("ts"),
            )
            return

        text = event.get("text", "")
        if not text:
            return

        is_change = _looks_like_change_request(text)

        session.state = "awaiting_response"
        sessions.update(session)

        status_msg = await say(text=":hourglass_flowing_sand: Thinking...")
        status_ts = status_msg.get("ts", "")
        channel = event.get("channel", "")

        async def on_progress(preview: str):
            truncated = preview[-3500:] if len(preview) > 3500 else preview
            try:
                await client.chat_update(
                    channel=channel,
                    ts=status_ts,
                    text=f":hourglass_flowing_sand: Working...\n\n{truncated}",
                )
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
                session.state = "awaiting_approval"
                session.pending_plan = result.output
                sessions.update(session)

                response = format_claude_response(result.output)
                await say(
                    text=f"*Proposed Plan:*\n\n{response}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": f"*Proposed Plan:*\n\n{response}"},
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": ":white_check_mark: Approve"},
                                    "style": "primary",
                                    "action_id": "approve_plan",
                                    "value": user_id,
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": ":x: Reject"},
                                    "style": "danger",
                                    "action_id": "reject_plan",
                                    "value": user_id,
                                },
                            ],
                        },
                    ],
                )
            else:
                session.state = "idle"
                sessions.update(session)

                response = format_claude_response(result.output)
                await say(text=response)

        except Exception as e:
            logger.error(f"Chat message failed: {e}", exc_info=True)
            session.state = "idle"
            sessions.update(session)
            await say(text=f"Error: {e}")

    @app.action("approve_plan")
    async def handle_approve(ack, body, say, client):
        await ack()
        user_id = body["user"]["id"]
        if not is_authorized(user_id, config):
            return

        session = sessions.get(user_id)
        if not session or session.state != "awaiting_approval":
            return

        session.state = "executing"
        sessions.update(session)

        # Remove buttons
        channel = body["channel"]["id"]
        ts = body["message"]["ts"]
        original_text = body["message"].get("text", "Plan approved.")
        await client.chat_update(channel=channel, ts=ts, text=original_text, blocks=[])

        await say(text=":hourglass_flowing_sand: Executing plan...")

        try:
            result = await claude.execute_plan(session.conversation_id)
            session.conversation_id = result.conversation_id or session.conversation_id
            session.state = "idle"
            session.pending_plan = ""
            sessions.update(session)

            status = ":white_check_mark: Done" if result.success else ":x: Failed"
            response = format_claude_response(result.output)
            await say(text=f"{status}\n\n{response}")

        except Exception as e:
            logger.error(f"Plan execution failed: {e}", exc_info=True)
            session.state = "idle"
            sessions.update(session)
            await say(text=f"Execution error: {e}")

    @app.action("reject_plan")
    async def handle_reject(ack, body, say, client):
        await ack()
        user_id = body["user"]["id"]
        if not is_authorized(user_id, config):
            return

        session = sessions.get(user_id)
        if not session or session.state != "awaiting_approval":
            return

        session.state = "idle"
        session.pending_plan = ""
        sessions.update(session)

        # Remove buttons
        channel = body["channel"]["id"]
        ts = body["message"]["ts"]
        await client.chat_update(channel=channel, ts=ts, text="Plan rejected.", blocks=[])
