from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS = "Read,Glob,Grep"
FULL_TOOLS = "Bash(*),Read,Write,Edit,Glob,Grep"

PLAN_PROMPT_PREFIX = (
    "Analyze and propose a plan for the following request, but do NOT make any changes yet. "
    "Show exactly what files you would modify and what changes you would make. "
    "Do NOT use Write, Edit, or Bash tools.\n\n"
)

EXECUTE_PROMPT = (
    "Go ahead and execute the plan you just proposed. "
    "Make the changes and commit when done."
)


@dataclass
class TaskResult:
    success: bool
    output: str
    conversation_id: str = ""


ProgressCallback = Callable[[str], Any] | None


class ClaudeBridge:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir

    async def run_claude(
        self,
        prompt: str,
        allowed_tools: str = FULL_TOOLS,
        conversation_id: str | None = None,
        on_progress: ProgressCallback = None,
    ) -> TaskResult:
        """Run claude CLI and return the result. Streams progress via callback."""
        cmd = [
            "claude",
            "-p", prompt,
            "--allowedTools", allowed_tools,
            "--output-format", "stream-json",
            "--verbose",
        ]
        if conversation_id:
            cmd.extend(["--resume", conversation_id])

        logger.info(f"Running claude: tools={allowed_tools}, resume={conversation_id}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.project_dir),
            env=self._clean_env(),
            limit=10 * 1024 * 1024,  # 10MB line buffer limit
        )

        assistant_text: list[str] = []
        conv_id_ref = [conversation_id or ""]
        last_progress_len = 0

        async def read_output():
            nonlocal last_progress_len
            buf = b""
            while True:
                try:
                    chunk = await asyncio.wait_for(process.stdout.read(65536), timeout=1800)
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                buf += chunk

                # Split on newlines, process complete lines
                while b"\n" in buf:
                    line_bytes, buf = buf.split(b"\n", 1)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Extract session ID
                    if event.get("type") == "system" and "session_id" in event:
                        conv_id_ref[0] = event["session_id"]
                    if event.get("type") == "result":
                        conv_id_ref[0] = event.get("session_id", conv_id_ref[0])
                        if "result" in event:
                            assistant_text.append(event["result"])

                    # Extract assistant text
                    if event.get("type") == "assistant" and "content" in event:
                        for block in event["content"]:
                            if block.get("type") == "text":
                                assistant_text.append(block["text"])
                            elif block.get("type") == "tool_use":
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                if tool_name == "Read":
                                    path = tool_input.get("file_path", "")
                                    assistant_text.append(f"[Reading {path.split('/')[-1]}...]")
                                elif tool_name == "Edit":
                                    path = tool_input.get("file_path", "")
                                    assistant_text.append(f"[Editing {path.split('/')[-1]}...]")
                                elif tool_name == "Write":
                                    path = tool_input.get("file_path", "")
                                    assistant_text.append(f"[Writing {path.split('/')[-1]}...]")
                                elif tool_name == "Glob":
                                    assistant_text.append(f"[Searching files...]")
                                elif tool_name == "Grep":
                                    assistant_text.append(f"[Searching code...]")
                                elif tool_name == "Bash":
                                    cmd = str(tool_input.get("command", ""))[:80]
                                    assistant_text.append(f"[Running: {cmd}]")
                                elif tool_name:
                                    assistant_text.append(f"[{tool_name}...]")

                    # Send progress on every event (rate-limited by time)
                    import time
                    now = time.time()
                    if on_progress and (not hasattr(read_output, '_last_update') or now - read_output._last_update > 5):
                        read_output._last_update = now
                        if assistant_text:
                            preview = "\n".join(assistant_text)[-2000:]
                            try:
                                await on_progress(preview)
                            except Exception:
                                pass

            # Process remaining buffer
            if buf.strip():
                line = buf.decode("utf-8", errors="replace").strip()
                try:
                    event = json.loads(line)
                    if event.get("type") == "result" and "result" in event:
                        assistant_text.append(event["result"])
                        conv_id_ref[0] = event.get("session_id", conv_id_ref[0])
                except json.JSONDecodeError:
                    pass

        try:
            await asyncio.wait_for(read_output(), timeout=1800)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            logger.error("Claude timed out after 30 minutes")
            return TaskResult(
                success=False,
                output="Claude timed out after 30 minutes",
                conversation_id=conv_id_ref[0],
            )

        await process.wait()
        exit_code = process.returncode

        full_output = "\n".join(assistant_text) if assistant_text else ""
        logger.info(f"Claude finished: exit={exit_code}, output_len={len(full_output)}")

        return TaskResult(
            success=exit_code == 0,
            output=full_output,
            conversation_id=conv_id_ref[0],
        )

    async def send_message(
        self, message: str, plan_only: bool = False,
        conversation_id: str | None = None,
        on_progress: ProgressCallback = None,
    ) -> TaskResult:
        if plan_only:
            prompt = PLAN_PROMPT_PREFIX + message
            tools = READ_ONLY_TOOLS
        else:
            prompt = message
            tools = FULL_TOOLS
        return await self.run_claude(prompt, tools, conversation_id, on_progress)

    async def execute_plan(self, conversation_id: str, on_progress: ProgressCallback = None) -> TaskResult:
        return await self.run_claude(EXECUTE_PROMPT, FULL_TOOLS, conversation_id, on_progress)

    async def run_task(self, title: str, description: str, issue_number: int = 0, on_progress: ProgressCallback = None) -> TaskResult:
        ref = f"\n- Reference issue #{issue_number} in your commit message (e.g. 'fix: ... refs #{issue_number}')" if issue_number else ""
        prompt = f"Task: {title}\n\n{description}\n\nImportant:\n- Work in this project directory\n- Follow the guidelines in CLAUDE.md\n- Commit your changes when the task is complete{ref}"
        return await self.run_claude(prompt, FULL_TOOLS, on_progress=on_progress)

    async def fix_from_test_output(
        self, test_output: str, conversation_id: str, on_progress: ProgressCallback = None
    ) -> TaskResult:
        prompt = (
            f"The following tests failed after your changes:\n\n"
            f"```\n{test_output[:8000]}\n```\n\n"
            f"Fix the issues and commit the fixes."
        )
        return await self.run_claude(prompt, FULL_TOOLS, conversation_id, on_progress)

    def _clean_env(self) -> dict[str, str]:
        import os
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        return env
