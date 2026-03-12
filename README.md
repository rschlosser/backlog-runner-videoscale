# Claude Code Runner

Telegram-controlled Claude Code runner with GitHub Issues as task source. Run Claude Code 24/7, manage tasks from your phone, and chat with Claude interactively.

## Features

- **GitHub Issues as backlog** -- tasks as Issues, priority/status via labels
- **Telegram bot** -- full control: add tasks, view status, pause/resume, retry, get notifications
- **Interactive chat mode** -- talk to Claude Code via Telegram with dry-run approval for changes
- **Test-until-green loop** -- automatically run tests after task completion, fix failures
- **Single Docker container** -- bot + runner in one process, easy to deploy

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env: set GITHUB_TOKEN, GITHUB_REPO, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, PROJECT_DIR

# 2. Create a Telegram bot via @BotFather, get the token

# 3. Get your Telegram user ID via @userinfobot

# 4. Run
docker compose up -d

# Or run locally:
pip install -r requirements.txt
python -m bot.main
```

## Telegram Commands

### Backlog Management
| Command | Description |
|---------|-------------|
| `/status` | Backlog summary + runner state |
| `/list [todo\|done\|failed]` | List tasks by status |
| `/add P1 Task title` | Create a new task (GitHub Issue) |
| `/detail #42` | Show full task info |
| `/logs #42` | Show execution logs |
| `/retry #42` | Reset failed task to TODO |
| `/pause` | Pause the runner |
| `/resume` | Resume the runner |

### Chat Mode
| Command | Description |
|---------|-------------|
| `/chat` | Start interactive Claude Code session |
| `/endchat` | End chat session |
| Any text | Forwarded to Claude Code |

In chat mode, change requests get a dry-run plan first. Approve or reject via inline buttons before Claude makes changes.

## GitHub Issue Format

Tasks are GitHub Issues with labels:
- **Priority**: `P0` (critical), `P1` (high), `P2` (medium), `P3` (low)
- **Status**: `backlog:todo`, `backlog:in-progress`, `backlog:done`, `backlog:failed`

Optional metadata in issue body:
```
depends: #41, #42
verify: pytest tests/ -x
```

## Architecture

```
GitHub Issues <----> Single Docker Container
                     |- Telegram bot (handlers)
                     |- Task runner (polls GitHub)
                     |- Claude bridge (CLI wrapper)
                     |- Test loop (deploy + verify)
                     |
                     Claude Code CLI installed
```

## Files

| Path | Description |
|------|-------------|
| `bot/main.py` | Entry point -- starts bot + runner as async tasks |
| `bot/config.py` | Configuration from environment variables |
| `bot/handlers/backlog.py` | Telegram commands for task management |
| `bot/handlers/chat.py` | Interactive chat mode with approval flow |
| `bot/services/github_tasks.py` | GitHub Issues API client |
| `bot/services/claude_bridge.py` | Claude Code CLI wrapper |
| `bot/services/runner.py` | Task runner with test-until-green loop |
| `bot/services/session_store.py` | Chat session persistence |
| `Dockerfile` | Container with Python + Node.js + Claude CLI |
| `docker-compose.yml` | Single-service Docker Compose |

## Legacy

`runner.sh` and `BACKLOG.md` are the original bash-based runner (kept for reference).
