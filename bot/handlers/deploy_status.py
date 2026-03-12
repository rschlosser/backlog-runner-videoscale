from __future__ import annotations

import asyncio
import logging
import os

import httpx
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from bot.auth import restricted
from bot.formatter import _escape_html

logger = logging.getLogger(__name__)

# Vercel projects to check
VERCEL_PROJECTS = [
    ("INT", "videoscale-int"),
    ("Stable", "videoscale-stable"),
    ("Prod", "videoscale-prod"),
]

# Railway environments
RAILWAY_PROJECT_ID = "352b4120-5a5d-4b7d-847d-c04b58e0bef5"
RAILWAY_ENVS = [
    ("INT", "7548a5e8-b992-4dca-aaaa-37776183e7b8"),
    ("Stable", "9fa89113-ec74-41cf-8733-625a1f7abcf9"),
    ("Prod", "32825af9-f776-4cf7-94de-e076b6378f75"),
]

PROJECT_DIR = os.environ.get("PROJECT_DIR", "/project")


async def _check_vercel() -> list[str]:
    """Check Vercel deployment status."""
    token = os.environ.get("VERCEL_TOKEN", "")
    if not token:
        return ["Vercel: no token configured"]

    lines = ["<b>Vercel (Frontend)</b>", "<pre>"]
    lines.append(f"{'Env':8s} {'State':8s} {'Branch':6s} Commit")
    async with httpx.AsyncClient(timeout=10) as client:
        for env_name, project in VERCEL_PROJECTS:
            try:
                resp = await client.get(
                    "https://api.vercel.com/v6/deployments",
                    params={"projectId": project, "limit": 1, "target": "production"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
                deps = data.get("deployments", [])
                if deps:
                    d = deps[0]
                    state = d.get("readyState", d.get("state", "?"))
                    commit = d.get("meta", {}).get("githubCommitSha", "")[:7]
                    branch = d.get("meta", {}).get("githubCommitRef", "?")
                    lines.append(_escape_html(
                        f"{env_name:8s} {state:8s} {branch:6s} {commit}"
                    ))
                else:
                    lines.append(_escape_html(f"{env_name:8s} no deployments"))
            except Exception as e:
                lines.append(_escape_html(f"{env_name:8s} error: {e}"))
    lines.append("</pre>")
    return lines


async def _check_railway() -> list[str]:
    """Check Railway deployment status."""
    token = os.environ.get("RAILWAY_API_TOKEN", "")
    if not token:
        return ["Railway: no token configured"]

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
                    createdAt
                    service { id, name }
                }
            }
        }
    }
    """

    lines = ["<b>Railway (Backend + Worker)</b>", "<pre>"]
    lines.append(f"{'Env':8s} {'Service':8s} {'Status':10s} Deployed")
    async with httpx.AsyncClient(timeout=10) as client:
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

                # Get latest deployment per service
                seen = set()
                service_deploys = []
                for edge in edges:
                    node = edge["node"]
                    svc = node.get("service") or {}
                    svc_id = svc.get("id", "")
                    if svc_id and svc_id not in seen:
                        seen.add(svc_id)
                        service_deploys.append(node)

                if service_deploys:
                    for node in service_deploys:
                        status = node.get("status", "?")
                        created = node.get("createdAt", "")[:16].replace("T", " ")
                        service = (node.get("service") or {}).get("name", "?")
                        lines.append(_escape_html(
                            f"{env_name:8s} {service:8s} {status:10s} {created}"
                        ))
                else:
                    lines.append(_escape_html(f"{env_name:8s} no deployments"))
            except Exception as e:
                lines.append(_escape_html(f"{env_name:8s} error: {e}"))
    lines.append("</pre>")
    return lines


async def _deploy_railway(env_name: str, env_id: str) -> list[str]:
    """Trigger Railway redeployment for all services in an environment."""
    token = os.environ.get("RAILWAY_API_TOKEN", "")
    if not token:
        return [f"Railway: no token configured"]

    # First get latest deployment for each service, then redeploy
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
                    id
                    status
                    service { id, name }
                }
            }
        }
    }
    """

    redeploy_mutation = """
    mutation($id: String!) {
        deploymentRedeploy(id: $id) {
            id
            status
        }
    }
    """

    lines = []
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Get current deployments
        resp = await client.post(
            "https://backboard.railway.app/graphql/v2",
            json={
                "query": query,
                "variables": {
                    "projectId": RAILWAY_PROJECT_ID,
                    "environmentId": env_id,
                },
            },
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        edges = data.get("data", {}).get("deployments", {}).get("edges", [])

        # Find latest deployment per service
        seen_services = set()
        to_redeploy = []
        for edge in edges:
            node = edge["node"]
            svc = node.get("service", {})
            svc_id = svc.get("id", "")
            if svc_id and svc_id not in seen_services:
                seen_services.add(svc_id)
                to_redeploy.append((node["id"], svc.get("name", "?")))

        if not to_redeploy:
            lines.append(f"  No deployments found for {env_name}")
            return lines

        # Redeploy each service
        for deploy_id, svc_name in to_redeploy:
            try:
                resp = await client.post(
                    "https://backboard.railway.app/graphql/v2",
                    json={
                        "query": redeploy_mutation,
                        "variables": {"id": deploy_id},
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                result = resp.json()
                if result.get("errors"):
                    err = result["errors"][0].get("message", "unknown")
                    lines.append(f"  {svc_name}: error - {err}")
                else:
                    lines.append(f"  {svc_name}: redeploying")
            except Exception as e:
                lines.append(f"  {svc_name}: error - {e}")

    return lines


async def _deploy_vercel(project: str) -> str:
    """Trigger a Vercel production deployment."""
    token = os.environ.get("VERCEL_TOKEN", "")
    if not token:
        return "no token"

    async with httpx.AsyncClient(timeout=30) as client:
        # Create a new deployment by triggering a redeploy of the latest
        resp = await client.get(
            "https://api.vercel.com/v6/deployments",
            params={"projectId": project, "limit": 1, "target": "production"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        deps = resp.json().get("deployments", [])
        if not deps:
            return "no deployments to redeploy"

        # Redeploy using the Vercel API
        deploy_id = deps[0]["uid"]
        resp = await client.post(
            f"https://api.vercel.com/v13/deployments",
            json={"name": project, "deploymentId": deploy_id, "target": "production"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            return f"error: {resp.status_code} {resp.text[:100]}"
        return "triggered"


async def _git_merge_and_push(source: str, target: str) -> tuple[bool, str]:
    """Merge source branch into target and push."""
    steps = [
        ["git", "fetch", "origin"],
        ["git", "checkout", target],
        ["git", "pull", "origin", target],
        ["git", "merge", f"origin/{source}", "--no-edit"],
        ["git", "push", "origin", target],
        ["git", "checkout", source],
    ]

    output_lines = []
    for cmd in steps:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=PROJECT_DIR,
        )
        stdout, _ = await proc.communicate()
        line = stdout.decode("utf-8", errors="replace").strip()
        output_lines.append(f"$ {' '.join(cmd)}\n{line}")
        if proc.returncode != 0:
            return False, "\n".join(output_lines)

    return True, "\n".join(output_lines)


def register_deploy_handlers(app, config, monitor=None):
    """Register deployment commands: /health, /stable, /prod, /monitor."""
    auth = restricted(config)

    @auth
    async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check deployment status across all environments."""
        msg = await update.message.reply_text("\u23f3 Checking deployments...")

        vercel_lines, railway_lines = await asyncio.gather(
            _check_vercel(),
            _check_railway(),
        )

        text = "\n".join(vercel_lines + [""] + railway_lines)
        await msg.edit_text(text, parse_mode="HTML")

    @auth
    async def cmd_stable(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Merge dev into main to trigger Stable deployment."""
        msg = await update.message.reply_text(
            "\u23f3 Merging <code>dev</code> \u2192 <code>main</code>...",
            parse_mode="HTML",
        )

        success, output = await _git_merge_and_push("dev", "main")

        if success:
            text = (
                "\u2705 <b>Stable deploy triggered!</b>\n\n"
                "Merged <code>dev</code> \u2192 <code>main</code> and pushed.\n"
                "Railway + Vercel Stable will auto-deploy from main."
            )
        else:
            # Truncate output for Telegram
            if len(output) > 3000:
                output = output[:3000] + "\n..."
            text = (
                "\u274c <b>Merge failed</b>\n\n"
                f"<pre>{_escape_html(output)}</pre>"
            )

        await msg.edit_text(text, parse_mode="HTML")

    @auth
    async def cmd_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Trigger manual production deployment."""
        msg = await update.message.reply_text(
            "\U0001f680 Deploying to <b>Production</b>...\n"
            "Frontend (Vercel) + Backend + Worker (Railway)",
            parse_mode="HTML",
        )

        # Deploy all prod services in parallel
        prod_env_id = "32825af9-f776-4cf7-94de-e076b6378f75"
        railway_lines, vercel_result = await asyncio.gather(
            _deploy_railway("Prod", prod_env_id),
            _deploy_vercel("videoscale-prod"),
        )

        lines = ["<b>Production Deploy</b>", ""]
        lines.append(f"<b>Vercel:</b> {_escape_html(vercel_result)}")
        lines.append("")
        lines.append("<b>Railway:</b>")
        lines.extend(_escape_html(l) for l in railway_lines)

        has_error = "error" in vercel_result or any("error" in l for l in railway_lines)
        icon = "\u274c" if has_error else "\u2705"
        status = "Some errors occurred" if has_error else "All services deploying"
        lines.insert(0, f"{icon} {status}\n")

        await msg.edit_text("\n".join(lines), parse_mode="HTML")

    @auth
    async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current health status of all monitored services."""
        if not monitor:
            await update.message.reply_text("Health monitoring not enabled.")
            return

        from bot.services.health_monitor import format_monitor_status

        statuses = monitor.get_current_status()
        text = format_monitor_status(statuses, monitor._last_check)
        await update.message.reply_text(text, parse_mode="HTML")

    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("stable", cmd_stable))
    app.add_handler(CommandHandler("prod", cmd_prod))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
