from __future__ import annotations

import base64
import logging
import re
import uuid
from typing import TYPE_CHECKING

import httpx

from bot.slack_auth import is_authorized

if TYPE_CHECKING:
    from bot.config import Config
    from bot.services.github_tasks import GitHubTaskManager

logger = logging.getLogger(__name__)


async def handle_image_message(event, say, config: Config, github: GitHubTaskManager) -> bool:
    """Handle a message with image attachments. Returns True if handled, False to pass through."""
    user_id = event.get("user", "")
    if not user_id or not is_authorized(user_id, config):
        return False

    files = event.get("files", [])
    if not files:
        return False

    # Ignore edits and subtypes (except file_share)
    subtype = event.get("subtype")
    if subtype and subtype != "file_share":
        return False

    # Filter to image files only
    image_files = [f for f in files if f.get("mimetype", "").startswith("image/")]
    if not image_files:
        return False

    text = event.get("text", "").strip()

    # Use the message text as title, or a default
    if text:
        lines = text.split("\n", 1)
        title = lines[0].strip()
        description = lines[1].strip() if len(lines) > 1 else ""
    else:
        title = "Bug report from Slack"
        description = ""

    # Download and upload images to GitHub
    image_urls = []
    for img_file in image_files:
        try:
            url = img_file.get("url_private_download") or img_file.get("url_private")
            if not url:
                continue

            logger.info(f"Downloading image from Slack: {img_file.get('name')} ({url[:80]}...)")
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url,
                    headers={"Authorization": f"Bearer {config.slack_bot_token}"},
                    follow_redirects=True,
                )
                resp.raise_for_status()
                image_data = resp.content
            logger.info(f"Downloaded {len(image_data)} bytes for {img_file.get('name')}")

            ext = img_file.get("filetype", "png")
            filename = f"{uuid.uuid4().hex[:12]}.{ext}"
            github_url = await _upload_image_to_github(github, filename, image_data)
            if github_url:
                image_urls.append((img_file.get("name", filename), github_url))

        except Exception as e:
            logger.error(f"Failed to process image {img_file.get('name')}: {e}")

    # Build issue body with embedded images
    body_parts = []
    if description:
        body_parts.append(description)
    if image_urls:
        body_parts.append("")
        body_parts.append("## Screenshots")
        for name, url in image_urls:
            body_parts.append(f"![{name}]({url})")

    body_parts.append("")
    body_parts.append(f"_Reported via Slack by <@{user_id}>_")

    full_body = "\n".join(body_parts)

    # Create GitHub issue as P2 by default (can be overridden with P0-P3 prefix)
    priority = 2
    prio_match = re.match(r"^P([0-3])\s+(.+)$", title)
    if prio_match:
        priority = int(prio_match.group(1))
        title = prio_match.group(2)

    task = await github.create_task(title, full_body, priority)
    await say(
        text=f":white_check_mark: Created issue #{task.number}: {task.title} (P{task.priority})"
        + (f" with {len(image_urls)} screenshot(s)" if image_urls else ""),
    )
    return True


async def _upload_image_to_github(
    github: GitHubTaskManager, filename: str, content: bytes
) -> str | None:
    """Upload an image to the GitHub repo and return the raw URL."""
    path = f".github/screenshots/{filename}"
    encoded = base64.b64encode(content).decode("ascii")

    try:
        resp = await github.client.put(
            f"{github.base_url}/contents/{path}",
            json={
                "message": f"chore: add screenshot {filename}",
                "content": encoded,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        sha = data.get("content", {}).get("sha", "")
        # Use the blob URL — works for logged-in users on private repos (no expiring token)
        url = f"https://github.com/{github.repo}/blob/main/{path}?raw=true"
        logger.info(f"Uploaded screenshot {filename} (sha={sha}) -> {url}")
        return url
    except Exception as e:
        logger.error(f"Failed to upload image to GitHub: {e}")
        if hasattr(e, "response") and e.response is not None:
            logger.error(f"Response: {e.response.status_code} {e.response.text[:500]}")
        return None
