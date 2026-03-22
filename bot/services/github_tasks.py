from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

PRIORITY_LABELS = {"P0", "P1", "P2", "P3"}
STATUS_LABELS = {
    "backlog:todo",
    "backlog:in-progress",
    "backlog:done",
    "backlog:failed",
}


@dataclass
class Task:
    number: int
    title: str
    body: str
    priority: int
    status: str  # "todo", "in-progress", "done", "failed"
    labels: list[str] = field(default_factory=list)
    verify_cmd: str = ""
    depends: list[int] = field(default_factory=list)

    @classmethod
    def from_issue(cls, issue: dict) -> Task:
        labels = [l["name"] for l in issue.get("labels", [])]

        priority = 3
        for label in labels:
            if label in PRIORITY_LABELS:
                priority = int(label[1])
                break

        status = "todo"
        for label in labels:
            if label.startswith("backlog:"):
                status = label.split(":", 1)[1]
                break

        body = issue.get("body", "") or ""

        verify_cmd = ""
        verify_match = re.search(r"^verify:\s*(.+)$", body, re.MULTILINE)
        if verify_match:
            verify_cmd = verify_match.group(1).strip()

        depends: list[int] = []
        depends_match = re.search(r"^depends:\s*(.+)$", body, re.MULTILINE)
        if depends_match:
            for ref in re.findall(r"#(\d+)", depends_match.group(1)):
                depends.append(int(ref))

        return cls(
            number=issue["number"],
            title=issue["title"],
            body=body,
            priority=priority,
            status=status,
            labels=labels,
            verify_cmd=verify_cmd,
            depends=depends,
        )


class GitHubTaskManager:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.base_url = f"https://api.github.com/repos/{repo}"
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30.0,
        )

    async def close(self):
        await self.client.aclose()

    async def _ensure_labels(self):
        """Create backlog labels if they don't exist."""
        all_labels = PRIORITY_LABELS | STATUS_LABELS
        resp = await self.client.get(f"{self.base_url}/labels", params={"per_page": 100})
        existing = {l["name"] for l in resp.json()} if resp.status_code == 200 else set()

        colors = {
            "P0": "b60205",
            "P1": "d93f0b",
            "P2": "fbca04",
            "P3": "0e8a16",
            "backlog:todo": "cfd3d7",
            "backlog:in-progress": "0075ca",
            "backlog:done": "0e8a16",
            "backlog:failed": "b60205",
        }

        for label in all_labels - existing:
            await self.client.post(
                f"{self.base_url}/labels",
                json={"name": label, "color": colors.get(label, "ededed")},
            )

    async def get_tasks(self, status: str | None = None) -> list[Task]:
        """Fetch issues with backlog labels, optionally filtered by status."""
        label_filter = f"backlog:{status}" if status else ""
        params: dict = {"state": "open", "per_page": 100}
        if label_filter:
            params["labels"] = label_filter

        # For non-filtered, get all backlog issues
        if not label_filter:
            tasks = []
            for s in ["todo", "in-progress", "failed"]:
                params["labels"] = f"backlog:{s}"
                resp = await self.client.get(f"{self.base_url}/issues", params=params)
                if resp.status_code == 200:
                    tasks.extend(Task.from_issue(i) for i in resp.json())
            # Also get done (closed)
            params["state"] = "closed"
            params["labels"] = "backlog:done"
            resp = await self.client.get(f"{self.base_url}/issues", params=params)
            if resp.status_code == 200:
                tasks.extend(Task.from_issue(i) for i in resp.json())
            return tasks

        if status == "done":
            params["state"] = "closed"

        resp = await self.client.get(f"{self.base_url}/issues", params=params)
        resp.raise_for_status()
        return [Task.from_issue(i) for i in resp.json()]

    async def get_todo_tasks(self) -> list[Task]:
        """Fetch todo tasks sorted by priority (P0 first)."""
        tasks = await self.get_tasks("todo")
        tasks.sort(key=lambda t: t.priority)
        return tasks

    async def find_similar_open(self, title: str) -> Task | None:
        """Check if an open issue with a similar title already exists."""
        # Search open backlog issues
        for status in ["todo", "in-progress", "failed"]:
            resp = await self.client.get(
                f"{self.base_url}/issues",
                params={"state": "open", "labels": f"backlog:{status}", "per_page": 50},
            )
            if resp.status_code == 200:
                for issue in resp.json():
                    if issue["title"].strip().lower() == title.strip().lower():
                        return Task.from_issue(issue)
        return None

    async def create_task(
        self, title: str, body: str, priority: int, depends: list[int] | None = None
    ) -> Task:
        """Create a new GitHub Issue as a backlog task."""
        await self._ensure_labels()

        full_body = body
        if depends:
            refs = ", ".join(f"#{n}" for n in depends)
            full_body += f"\n\ndepends: {refs}"

        resp = await self.client.post(
            f"{self.base_url}/issues",
            json={
                "title": title,
                "body": full_body,
                "labels": [f"P{priority}", "backlog:todo"],
            },
        )
        resp.raise_for_status()
        return Task.from_issue(resp.json())

    async def update_status(self, issue_number: int, new_status: str):
        """Swap status label on an issue."""
        # Get current labels
        resp = await self.client.get(f"{self.base_url}/issues/{issue_number}")
        resp.raise_for_status()
        issue = resp.json()
        current_labels = [l["name"] for l in issue.get("labels", [])]

        # Remove old status labels, add new one
        new_labels = [l for l in current_labels if not l.startswith("backlog:")]
        new_labels.append(f"backlog:{new_status}")

        update: dict = {"labels": new_labels}
        if new_status == "done":
            update["state"] = "closed"
        elif new_status in ("todo", "in-progress"):
            update["state"] = "open"

        resp = await self.client.patch(
            f"{self.base_url}/issues/{issue_number}", json=update
        )
        resp.raise_for_status()
        logger.info(f"Issue #{issue_number} status -> {new_status}")

    async def add_comment(self, issue_number: int, body: str):
        """Add a comment to an issue (used for execution logs)."""
        resp = await self.client.post(
            f"{self.base_url}/issues/{issue_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()

    async def count_failure_comments(self, issue_number: int) -> int:
        """Count how many '## ❌ Task Failed' comments exist on an issue."""
        count = 0
        page = 1
        while True:
            resp = await self.client.get(
                f"{self.base_url}/issues/{issue_number}/comments",
                params={"per_page": 100, "page": page},
            )
            if resp.status_code != 200:
                break
            comments = resp.json()
            if not comments:
                break
            for c in comments:
                if c.get("body", "").startswith("## ❌ Task Failed"):
                    count += 1
            page += 1
        return count

    async def get_task_detail(self, issue_number: int) -> Task:
        """Fetch a single issue by number."""
        resp = await self.client.get(f"{self.base_url}/issues/{issue_number}")
        resp.raise_for_status()
        return Task.from_issue(resp.json())

    def check_deps_satisfied(self, task: Task, all_tasks: list[Task]) -> bool:
        """Check if all dependencies of a task are done."""
        if not task.depends:
            return True
        done_numbers = {t.number for t in all_tasks if t.status == "done"}
        return all(dep in done_numbers for dep in task.depends)
