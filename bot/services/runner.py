from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from bot.config import Config
    from bot.services.claude_bridge import ClaudeBridge
    from bot.services.github_tasks import GitHubTaskManager, Task

logger = logging.getLogger(__name__)


class TaskRunner:
    def __init__(
        self,
        config: Config,
        github: GitHubTaskManager,
        claude: ClaudeBridge,
        notify: Callable[[str], asyncio.coroutine] | None = None,
    ):
        self.config = config
        self.github = github
        self.claude = claude
        self.notify = notify  # async callback to send Telegram notifications
        self._paused = False
        self._running = False
        self._current_task: Task | None = None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def current_task(self) -> Task | None:
        return self._current_task

    def pause(self):
        self._paused = True
        logger.info("Runner paused")

    def resume(self):
        self._paused = False
        logger.info("Runner resumed")

    def stop(self):
        self._running = False

    async def _notify(self, message: str):
        if self.notify:
            try:
                await self.notify(message)
            except Exception as e:
                logger.error(f"Notification failed: {e}")

    async def run_loop(self):
        """Main loop: poll GitHub Issues, execute next task, repeat."""
        self._running = True
        logger.info(f"Task runner started (interval={self.config.runner_interval}s)")

        while self._running:
            if self._paused:
                await asyncio.sleep(self.config.runner_interval)
                continue

            try:
                await self._process_next_task()
            except Exception as e:
                logger.error(f"Error in runner loop: {e}", exc_info=True)
                await self._notify(f"\u274c Runner error: {e}")

            await asyncio.sleep(self.config.runner_interval)

    async def _process_next_task(self):
        """Find and execute the next available task."""
        todo_tasks = await self.github.get_todo_tasks()
        if not todo_tasks:
            return

        all_tasks = await self.github.get_tasks()

        for task in todo_tasks:
            if self.github.check_deps_satisfied(task, all_tasks):
                await self._execute_task(task)
                return

        logger.debug("No tasks with satisfied dependencies")

    async def _pull_latest(self):
        """Pull latest changes from the remote branch before starting a task."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "pull", "--ff-only",
                cwd=str(self.config.project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace").strip()
            if process.returncode == 0:
                logger.info(f"Git pull: {output}")
            else:
                logger.warning(f"Git pull failed (rc={process.returncode}): {output}")
        except Exception as e:
            logger.warning(f"Git pull error: {e}")

    async def _execute_task(self, task: Task):
        """Execute a single task with optional verification loop."""
        # Re-check status to avoid duplicate execution (e.g. after container restart)
        fresh = await self.github.get_task_detail(task.number)
        if fresh.status not in ("todo", "failed"):
            logger.info(f"Skipping #{task.number} — already {fresh.status}")
            return

        self._current_task = task
        logger.info(f"Starting task #{task.number}: {task.title}")

        await self._pull_latest()
        await self.github.update_status(task.number, "in-progress")
        issue_url = f"https://github.com/{self.github.repo}/issues/{task.number}"
        await self._notify(f"\u23f3 Started: #{task.number} {task.title}\n{issue_url}")

        async def on_progress(preview: str):
            short = preview[-500:] if len(preview) > 500 else preview
            await self._notify(f"\u2699\ufe0f #{task.number} progress:\n{short}")

        try:
            result = await self.claude.run_task(task.title, task.body, issue_number=task.number, on_progress=on_progress)

            if not result.success:
                # If auth expired, pause runner to avoid burning retries
                if "authentication expired" in result.output.lower() or "auth" in result.output.lower() and "login" in result.output.lower():
                    self._paused = True
                    await self._notify(
                        "🔑 Claude authentication expired! Runner paused.\n"
                        "Run `claude auth login` on the server, then /resume."
                    )
                await self._handle_failure(task, result.output)
                return

            # Run verification loop if verify command is configured
            verify_cmd = task.verify_cmd or self.config.default_verify_cmd
            if verify_cmd:
                success = await self._verify_loop(task, result.conversation_id, verify_cmd)
                if not success:
                    return

            # Push changes to remote — only mark done if push succeeds
            push_ok = await self._push_changes()
            if not push_ok:
                await self._handle_failure(task, "Task completed but git push failed. Changes are committed locally but not on remote.")
                return

            await self.github.update_status(task.number, "done")
            summary = result.output[:2000] if result.output else "Task completed"
            await self.github.add_comment(
                task.number,
                f"## \u2705 Task Completed\n\n{summary}",
            )
            short_summary = result.output[:1500] if result.output else "Task completed"
            await self._notify(f"\u2705 Completed: #{task.number} {task.title}\n{issue_url}\n\n{short_summary}")

        except Exception as e:
            logger.error(f"Task #{task.number} failed with exception: {e}", exc_info=True)
            await self._handle_failure(task, str(e))
        finally:
            self._current_task = None

    async def _verify_loop(
        self, task: Task, conversation_id: str, verify_cmd: str
    ) -> bool:
        """Run tests, feed failures back to Claude, repeat until green."""
        max_attempts = self.config.max_verify_retries
        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"Verification attempt {attempt}/{max_attempts} for #{task.number}"
            )
            logger.info(f"Running verify command: {verify_cmd}")
            test_output = await self._run_verify_cmd(verify_cmd)

            if test_output is None:
                logger.info(f"Verification passed for #{task.number}")
                return True

            logger.warning(
                f"Verification failed for #{task.number} (attempt {attempt}): "
                f"{test_output[:200]}"
            )

            # On the last attempt, don't bother fixing — just fail
            if attempt == max_attempts:
                break

            await self._notify(
                f"\u26a0\ufe0f Tests failed for #{task.number} "
                f"(attempt {attempt}/{max_attempts}). Claude is fixing..."
            )

            fix_result = await self.claude.fix_from_test_output(
                test_output, conversation_id
            )
            if not fix_result.success:
                await self._handle_failure(
                    task, f"Claude failed to fix test failures:\n{fix_result.output}"
                )
                return False

            conversation_id = fix_result.conversation_id or conversation_id

        await self._handle_failure(
            task,
            f"Verification failed after {max_attempts} attempts",
        )
        return False

    async def _push_changes(self) -> bool:
        """Push committed changes to remote. Returns True on success."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "push",
                cwd=str(self.config.project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace").strip()
            if process.returncode == 0:
                logger.info(f"Git push: {output}")
                return True
            else:
                logger.error(f"Git push failed (rc={process.returncode}): {output}")
                return False
        except Exception as e:
            logger.error(f"Git push error: {e}")
            return False

    async def _run_verify_cmd(self, cmd: str) -> str | None:
        """Run a verification command. Returns None if passed, or output if failed."""
        # Replace Docker-style /project paths with actual project dir
        cmd = cmd.replace("/project", str(self.config.project_dir))
        # Strip redundant cd into project dir since cwd is already set
        cmd = cmd.replace(f"cd {self.config.project_dir} && ", "")
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.config.project_dir),
        )
        stdout, _ = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")

        if process.returncode == 0:
            return None
        return output

    async def _handle_failure(self, task: Task, error_output: str):
        truncated = error_output[:3000] if error_output else "Unknown error"
        await self.github.add_comment(
            task.number,
            f"## \u274c Task Failed\n\n```\n{truncated}\n```",
        )

        # Check if we should auto-retry
        failure_count = await self.github.count_failure_comments(task.number)
        max_retries = self.config.max_task_retries
        issue_url = f"https://github.com/{self.github.repo}/issues/{task.number}"
        short_error = error_output[:1000] if error_output else "Unknown error"

        if failure_count < max_retries:
            # Re-queue for another attempt
            await self.github.update_status(task.number, "todo")
            await self._notify(
                f"\u26a0\ufe0f Failed: #{task.number} {task.title} "
                f"(attempt {failure_count}/{max_retries} — auto-retrying)\n"
                f"{issue_url}\n\n```\n{short_error}\n```"
            )
            logger.info(f"Task #{task.number} auto-retry {failure_count}/{max_retries}")
        else:
            # Max retries exhausted — mark as permanently failed
            await self.github.update_status(task.number, "failed")
            await self._notify(
                f"\u274c Failed: #{task.number} {task.title} "
                f"(gave up after {max_retries} attempts)\n"
                f"{issue_url}\n\n```\n{short_error}\n```"
            )
            logger.warning(f"Task #{task.number} permanently failed after {max_retries} attempts")

    async def run_single(self, issue_number: int):
        """Run a specific task (triggered via /retry or manual)."""
        task = await self.github.get_task_detail(issue_number)
        if task.status == "failed":
            await self.github.update_status(issue_number, "todo")
            task.status = "todo"
        await self._execute_task(task)
