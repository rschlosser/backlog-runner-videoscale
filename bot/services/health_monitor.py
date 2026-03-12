from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# Endpoints to monitor
API_ENDPOINTS = [
    ("API INT", "https://backend-int.up.railway.app/health"),
    ("API Stable", "https://backend-stable.up.railway.app/health"),
    ("API Prod", "https://backend-prod-1.up.railway.app/health"),
]

FRONTEND_ENDPOINTS = [
    ("Frontend INT", "https://videoscale-int.vercel.app"),
    ("Frontend Stable", "https://videoscale-stable.vercel.app"),
    ("Frontend Prod", "https://videoscale.ai"),
]

GITHUB_REPO = "rschlosser/videoscale"
GITHUB_DEPLOY_WORKFLOW = "deploy.yml"

RAILWAY_PROJECT_ID = "352b4120-5a5d-4b7d-847d-c04b58e0bef5"
RAILWAY_ENVS = [
    ("INT", "7548a5e8-b992-4dca-aaaa-37776183e7b8"),
    ("Stable", "9fa89113-ec74-41cf-8733-625a1f7abcf9"),
    ("Prod", "32825af9-f776-4cf7-94de-e076b6378f75"),
]


@dataclass
class ServiceStatus:
    name: str
    status: str  # "ok", "degraded", "down"
    detail: str = ""
    response_ms: int = 0


class HealthMonitor:
    def __init__(
        self,
        notify: Callable[[str], object] | None = None,
        interval: int = 300,
    ):
        self.notify = notify
        self.interval = interval
        self._running = False
        self._previous: dict[str, str] = {}  # name -> status
        self._current: dict[str, ServiceStatus] = {}
        self._last_check: float = 0

    def stop(self):
        self._running = False

    def get_current_status(self) -> dict[str, ServiceStatus]:
        return dict(self._current)

    async def run_loop(self):
        self._running = True
        logger.info(f"Health monitor started (interval={self.interval}s)")

        # Wait a bit before first check to let everything initialize
        await asyncio.sleep(30)

        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Health monitor error: {e}", exc_info=True)

            await asyncio.sleep(self.interval)

    async def _check_all(self):
        checks = []
        checks.extend(self._check_api(name, url) for name, url in API_ENDPOINTS)
        checks.extend(self._check_frontend(name, url) for name, url in FRONTEND_ENDPOINTS)
        checks.append(self._check_railway())
        checks.append(self._check_github_deploys())

        results = await asyncio.gather(*checks, return_exceptions=True)

        statuses: list[ServiceStatus] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Check failed: {result}")
            elif isinstance(result, list):
                statuses.extend(result)
            elif isinstance(result, ServiceStatus):
                statuses.append(result)

        # Update current status and detect transitions
        for s in statuses:
            self._current[s.name] = s
            prev = self._previous.get(s.name)

            if prev and prev != s.status:
                await self._alert(s, prev)

            self._previous[s.name] = s.status

        self._last_check = time.time()
        logger.info(
            f"Health check complete: "
            + ", ".join(f"{s.name}={s.status}" for s in statuses)
        )

    async def _check_api(self, name: str, url: str) -> ServiceStatus:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                start = time.monotonic()
                resp = await client.get(url)
                ms = int((time.monotonic() - start) * 1000)

                if resp.status_code != 200:
                    return ServiceStatus(name, "down", f"HTTP {resp.status_code}", ms)

                data = resp.json()
                status = data.get("status", "unknown")

                if status == "healthy":
                    return ServiceStatus(name, "ok", f"redis={data.get('redis', '?')}", ms)
                elif status == "degraded":
                    return ServiceStatus(name, "degraded", f"redis={data.get('redis', '?')}", ms)
                else:
                    return ServiceStatus(name, "down", f"status={status}", ms)

        except httpx.TimeoutException:
            return ServiceStatus(name, "down", "timeout")
        except Exception as e:
            return ServiceStatus(name, "down", str(e)[:100])

    async def _check_frontend(self, name: str, url: str) -> ServiceStatus:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                start = time.monotonic()
                resp = await client.get(url)
                ms = int((time.monotonic() - start) * 1000)

                if resp.status_code < 400:
                    return ServiceStatus(name, "ok", f"HTTP {resp.status_code}", ms)
                else:
                    return ServiceStatus(name, "down", f"HTTP {resp.status_code}", ms)

        except httpx.TimeoutException:
            return ServiceStatus(name, "down", "timeout")
        except Exception as e:
            return ServiceStatus(name, "down", str(e)[:100])

    async def _check_railway(self) -> list[ServiceStatus]:
        token = os.environ.get("RAILWAY_API_TOKEN", "")
        if not token:
            return []

        query = """
        query($projectId: String!, $environmentId: String!) {
            deployments(
                first: 10,
                input: {
                    projectId: $projectId,
                    environmentId: $environmentId
                }
            ) {
                edges {
                    node {
                        status
                        service { id, name }
                    }
                }
            }
        }
        """

        statuses = []
        async with httpx.AsyncClient(timeout=15) as client:
            for env_name, env_id in RAILWAY_ENVS:
                try:
                    resp = await client.post(
                        "https://backboard.railway.app/graphql/v2",
                        json={
                            "query": query,
                            "variables": {
                                "projectId": RAILWAY_PROJECT_ID,
                                "environmentId": env_id,
                            },
                        },
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    edges = data.get("data", {}).get("deployments", {}).get("edges", [])

                    # Latest deployment per service
                    seen = set()
                    for edge in edges:
                        node = edge["node"]
                        svc = node.get("service") or {}
                        svc_id = svc.get("id", "")
                        svc_name = svc.get("name", "?")
                        if svc_id and svc_id not in seen:
                            seen.add(svc_id)
                            deploy_status = node.get("status", "?")
                            name = f"Railway {env_name} {svc_name}"

                            if deploy_status in ("CRASHED", "FAILED", "REMOVING"):
                                statuses.append(ServiceStatus(name, "down", deploy_status))
                            elif deploy_status == "SUCCESS":
                                statuses.append(ServiceStatus(name, "ok", deploy_status))
                            else:
                                statuses.append(ServiceStatus(name, "ok", deploy_status))

                except Exception as e:
                    logger.warning(f"Railway API check failed for {env_name}: {e}")
                    # Don't mark as "down" — actual service health is checked via API endpoints
                    statuses.append(
                        ServiceStatus(f"Railway {env_name}", "degraded", f"API unreachable")
                    )

        return statuses

    async def _check_github_deploys(self) -> list[ServiceStatus]:
        """Check latest GitHub Actions deploy workflow runs per branch/env."""
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return []

        statuses = []
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
            # INT: push to dev, Stable: push to main, Prod: workflow_dispatch to main
            checks = [
                ("dev", "push", "INT"),
                ("main", "push", "Stable"),
                ("main", "workflow_dispatch", "Prod"),
            ]
            for branch, event_filter, env_label in checks:
                try:
                    resp = await client.get(
                        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{GITHUB_DEPLOY_WORKFLOW}/runs",
                        params={"branch": branch, "event": event_filter, "per_page": 1},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    runs = resp.json().get("workflow_runs", [])
                    name = f"Deploy {env_label}"

                    if not runs:
                        statuses.append(ServiceStatus(name, "ok", "no runs"))
                        continue

                    run = runs[0]
                    conclusion = run.get("conclusion")
                    status = run.get("status")
                    sha = run.get("head_sha", "")[:7]

                    if status != "completed":
                        statuses.append(ServiceStatus(name, "ok", f"running ({sha})"))
                    elif conclusion == "success":
                        statuses.append(ServiceStatus(name, "ok", f"passed ({sha})"))
                    elif conclusion == "failure":
                        statuses.append(ServiceStatus(name, "down", f"FAILED ({sha})"))
                    elif conclusion == "cancelled":
                        statuses.append(ServiceStatus(name, "degraded", f"cancelled ({sha})"))
                    else:
                        statuses.append(ServiceStatus(name, "ok", f"{conclusion} ({sha})"))

                except Exception as e:
                    statuses.append(
                        ServiceStatus(f"Deploy {env_label}", "down", str(e)[:100])
                    )

        return statuses

    async def _alert(self, current: ServiceStatus, previous_status: str):
        if not self.notify:
            return

        if current.status == "ok":
            msg = f"\U0001f7e2 {current.name} recovered ({current.response_ms}ms)"
        elif current.status == "degraded":
            msg = f"\U0001f7e1 {current.name} is DEGRADED \u2014 {current.detail}"
        else:
            msg = f"\U0001f534 {current.name} is DOWN \u2014 {current.detail}"

        logger.warning(msg)
        try:
            await self.notify(msg)
        except Exception as e:
            logger.error(f"Alert notification failed: {e}")

    async def _send_notify(self, message: str):
        if self.notify:
            try:
                await self.notify(message)
            except Exception as e:
                logger.error(f"Notification failed: {e}")


def format_monitor_status(statuses: dict[str, ServiceStatus], last_check: float) -> str:
    """Format current status for /monitor command (HTML)."""
    if not statuses:
        return "No health data yet. First check runs 30s after startup."

    icons = {"ok": "\u2705", "degraded": "\u26a0\ufe0f", "down": "\u274c"}

    lines = ["<b>Service Health</b>", ""]

    # Group by category
    api = [(n, s) for n, s in statuses.items() if n.startswith("API")]
    frontend = [(n, s) for n, s in statuses.items() if n.startswith("Frontend")]
    railway = [(n, s) for n, s in statuses.items() if n.startswith("Railway")]
    deploys = [(n, s) for n, s in statuses.items() if n.startswith("Deploy")]

    for group_name, group in [("API", api), ("Frontend", frontend), ("Railway", railway), ("CI/CD", deploys)]:
        if not group:
            continue
        lines.append(f"<b>{group_name}</b>")
        for name, s in sorted(group):
            icon = icons.get(s.status, "?")
            ms = f" ({s.response_ms}ms)" if s.response_ms else ""
            detail = f" \u2014 {s.detail}" if s.detail else ""
            lines.append(f"  {icon} {name}{ms}{detail}")
        lines.append("")

    if last_check:
        ago = int(time.time() - last_check)
        lines.append(f"<i>Last check: {ago}s ago</i>")

    return "\n".join(lines)
