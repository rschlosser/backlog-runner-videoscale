#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Claude Code Backlog Runner
# Reads tasks from BACKLOG.md and executes them via Claude Code
# in headless mode (-p flag).
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKLOG_FILE="${SCRIPT_DIR}/BACKLOG.md"
STATE_DIR="${SCRIPT_DIR}/.state"
LOG_DIR="${SCRIPT_DIR}/logs"
LOCK_FILE="${SCRIPT_DIR}/.runner.lock"

# Load .env if present
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  set -a
  source "${SCRIPT_DIR}/.env"
  set +a
fi

# Configuration with defaults
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
RUNNER_INTERVAL="${RUNNER_INTERVAL:-30}"
MAX_RETRIES="${MAX_RETRIES:-3}"
LOG_LEVEL="${LOG_LEVEL:-info}"
WORKER_ID="${WORKER_ID:-worker-1}"
DRY_RUN=false
SINGLE_RUN=false

# Rate limit backoff
BACKOFF_BASE=60
BACKOFF_MAX=900
BACKOFF_CURRENT=$BACKOFF_BASE

# ---- Logging ----

log() {
  local level="$1"
  shift
  local timestamp
  timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
  mkdir -p "$LOG_DIR"
  echo "[$timestamp] [$WORKER_ID] [$level] $*" | tee -a "${LOG_DIR}/runner.log"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }
log_debug() {
  if [[ "$LOG_LEVEL" == "debug" ]]; then
    log "DEBUG" "$@"
  fi
}

# ---- Setup ----

setup() {
  mkdir -p "$STATE_DIR" "$LOG_DIR"

  if [[ ! -f "$BACKLOG_FILE" ]]; then
    log_error "BACKLOG.md not found at $BACKLOG_FILE"
    exit 1
  fi

  if ! command -v claude &>/dev/null; then
    log_error "claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
    exit 1
  fi
}

# ---- Locking ----

acquire_lock() {
  if [[ -f "$LOCK_FILE" ]]; then
    local lock_pid
    lock_pid="$(cat "$LOCK_FILE" 2>/dev/null || echo "")"
    if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
      log_warn "Another runner (PID $lock_pid) is active. Waiting..."
      return 1
    else
      log_info "Stale lock found (PID $lock_pid). Removing."
      rm -f "$LOCK_FILE"
    fi
  fi
  echo $$ > "$LOCK_FILE"
  return 0
}

release_lock() {
  rm -f "$LOCK_FILE"
}

# ---- BACKLOG.md Parsing ----

# Parse all tasks from BACKLOG.md
# Output format: STATUS|TASK_ID|PRIORITY|TITLE|DEPENDS|LINE_NUMBER
parse_backlog() {
  local line_num=0
  local current_status="" current_id="" current_prio="" current_title="" current_depends="none"
  local in_task=false

  while IFS= read -r line; do
    ((line_num++))

    # Match task header: ## [STATUS] TASK-XXX | P0-P3 | Title
    if [[ "$line" =~ ^##[[:space:]]+\[([A-Z_]+)\][[:space:]]+(TASK-[0-9]+)[[:space:]]*\|[[:space:]]*P([0-3])[[:space:]]*\|[[:space:]]*(.*) ]]; then
      # Emit previous task if any
      if $in_task; then
        echo "${current_status}|${current_id}|${current_prio}|${current_title}|${current_depends}|${task_line}"
      fi

      current_status="${BASH_REMATCH[1]}"
      current_id="${BASH_REMATCH[2]}"
      current_prio="${BASH_REMATCH[3]}"
      current_title="${BASH_REMATCH[4]}"
      current_depends="none"
      task_line=$line_num
      in_task=true
      continue
    fi

    # Match depends line
    if $in_task && [[ "$line" =~ ^depends:[[:space:]]*(.*) ]]; then
      current_depends="${BASH_REMATCH[1]}"
    fi
  done < "$BACKLOG_FILE"

  # Emit last task
  if $in_task; then
    echo "${current_status}|${current_id}|${current_prio}|${current_title}|${current_depends}|${task_line}"
  fi
}

# Get task description (lines between header and next header)
get_task_description() {
  local task_id="$1"
  local capture=false
  local description=""

  while IFS= read -r line; do
    if [[ "$line" =~ ^##[[:space:]]+\[[A-Z_]+\][[:space:]]+"$task_id" ]]; then
      capture=true
      continue
    fi
    if $capture; then
      # Stop at next task header or end
      if [[ "$line" =~ ^##[[:space:]]+\[ ]]; then
        break
      fi
      # Skip depends line and separators
      if [[ "$line" =~ ^depends: ]] || [[ "$line" =~ ^---[[:space:]]*$ ]]; then
        continue
      fi
      description+="${line}"$'\n'
    fi
  done < "$BACKLOG_FILE"

  echo "$description" | sed -e 's/^[[:space:]]*//' -e '/^$/d'
}

# Check if all dependencies of a task are DONE
deps_satisfied() {
  local depends="$1"

  if [[ "$depends" == "none" || -z "$depends" ]]; then
    return 0
  fi

  local all_tasks
  all_tasks="$(parse_backlog)"

  IFS=',' read -ra dep_list <<< "$depends"
  for dep in "${dep_list[@]}"; do
    dep="$(echo "$dep" | xargs)"  # trim whitespace
    local dep_status
    dep_status="$(echo "$all_tasks" | grep "|${dep}|" | cut -d'|' -f1)"
    if [[ "$dep_status" != "DONE" ]]; then
      return 1
    fi
  done
  return 0
}

# Find the next task to run (highest priority TODO with satisfied deps)
next_task() {
  local tasks
  tasks="$(parse_backlog | grep '^TODO|' | sort -t'|' -k3,3n)"

  while IFS='|' read -r status id prio title depends line_num; do
    if deps_satisfied "$depends"; then
      echo "${id}|${prio}|${title}|${depends}|${line_num}"
      return 0
    else
      log_debug "Skipping $id — dependencies not met: $depends"
    fi
  done <<< "$tasks"

  return 1
}

# ---- Status Updates ----

update_task_status() {
  local task_id="$1"
  local old_status="$2"
  local new_status="$3"

  sed -i "s/\[${old_status}\] ${task_id}/[${new_status}] ${task_id}/" "$BACKLOG_FILE"
  log_info "Updated $task_id: [$old_status] -> [$new_status]"
}

# ---- State Management ----

save_state() {
  local task_id="$1"
  local status="$2"
  local attempt="${3:-1}"

  cat > "${STATE_DIR}/${task_id}.json" <<EOF
{
  "task_id": "${task_id}",
  "status": "${status}",
  "attempt": ${attempt},
  "worker": "${WORKER_ID}",
  "timestamp": "$(date -Iseconds)",
  "pid": $$
}
EOF
}

load_state() {
  local task_id="$1"
  local state_file="${STATE_DIR}/${task_id}.json"
  if [[ -f "$state_file" ]]; then
    cat "$state_file"
  fi
}

get_attempt_count() {
  local task_id="$1"
  local state
  state="$(load_state "$task_id")"
  if [[ -n "$state" ]]; then
    echo "$state" | grep -o '"attempt": [0-9]*' | grep -o '[0-9]*'
  else
    echo "0"
  fi
}

# ---- Task Execution ----

run_task() {
  local task_id="$1"
  local title="$2"
  local description
  description="$(get_task_description "$task_id")"

  # Build the prompt
  local prompt="Task: ${title}

${description}

Important:
- Work in the project directory: ${PROJECT_DIR}
- Follow the guidelines in CLAUDE.md
- Commit your changes when the task is complete
- Task ID: ${task_id}"

  if $DRY_RUN; then
    log_info "[DRY RUN] Would execute $task_id: $title"
    log_debug "[DRY RUN] Prompt: $prompt"
    return 0
  fi

  local attempt
  attempt=$(($(get_attempt_count "$task_id") + 1))

  if (( attempt > MAX_RETRIES )); then
    log_error "$task_id exceeded max retries ($MAX_RETRIES). Marking FAILED."
    update_task_status "$task_id" "TODO" "FAILED"
    save_state "$task_id" "FAILED" "$attempt"
    return 1
  fi

  log_info "Starting $task_id (attempt $attempt/$MAX_RETRIES): $title"
  update_task_status "$task_id" "TODO" "IN_PROGRESS"
  save_state "$task_id" "IN_PROGRESS" "$attempt"

  local task_log="${LOG_DIR}/${task_id}_$(date '+%Y%m%d_%H%M%S').log"

  # Execute via Claude Code headless mode
  local exit_code=0
  claude -p "$prompt" \
    --allowedTools "Bash(*),Read,Write,Edit,Glob,Grep" \
    --output-format stream-json \
    2>&1 | tee "$task_log" || exit_code=$?

  if (( exit_code == 0 )); then
    log_info "$task_id completed successfully."
    update_task_status "$task_id" "IN_PROGRESS" "DONE"
    save_state "$task_id" "DONE" "$attempt"
    BACKOFF_CURRENT=$BACKOFF_BASE  # reset backoff on success
    return 0
  else
    log_error "$task_id failed (exit code $exit_code). See $task_log"

    # Check for rate limit (exit code 2 or specific output)
    if (( exit_code == 2 )) || grep -qi "rate.limit\|too many requests\|429" "$task_log" 2>/dev/null; then
      log_warn "Rate limited. Backing off for ${BACKOFF_CURRENT}s..."
      update_task_status "$task_id" "IN_PROGRESS" "TODO"
      save_state "$task_id" "RATE_LIMITED" "$attempt"
      sleep "$BACKOFF_CURRENT"
      BACKOFF_CURRENT=$(( BACKOFF_CURRENT * 2 ))
      if (( BACKOFF_CURRENT > BACKOFF_MAX )); then
        BACKOFF_CURRENT=$BACKOFF_MAX
      fi
      return 1
    fi

    update_task_status "$task_id" "IN_PROGRESS" "TODO"
    save_state "$task_id" "FAILED_ATTEMPT" "$attempt"
    return 1
  fi
}

# ---- Recovery ----

recover_interrupted() {
  log_info "Checking for interrupted tasks..."
  local tasks
  tasks="$(parse_backlog | grep '^IN_PROGRESS|')" || true

  while IFS='|' read -r status id prio title depends line_num; do
    if [[ -n "$id" ]]; then
      log_warn "Found interrupted task: $id. Resetting to TODO."
      update_task_status "$id" "IN_PROGRESS" "TODO"
    fi
  done <<< "$tasks"
}

# ---- Main Loop ----

show_status() {
  echo ""
  echo "=== Backlog Status ==="
  local tasks
  tasks="$(parse_backlog)"
  local todo=0 in_progress=0 done_count=0 failed=0

  while IFS='|' read -r status id prio title depends line_num; do
    case "$status" in
      TODO)        todo=$((todo + 1)) ;;
      IN_PROGRESS) in_progress=$((in_progress + 1)) ;;
      DONE)        done_count=$((done_count + 1)) ;;
      FAILED)      failed=$((failed + 1)) ;;
    esac
  done <<< "$tasks"

  echo "  TODO:        $todo"
  echo "  IN_PROGRESS: $in_progress"
  echo "  DONE:        $done_count"
  echo "  FAILED:      $failed"
  echo "======================"
  echo ""
}

run_dry() {
  setup
  show_status

  log_info "[DRY RUN] Listing all executable tasks in priority order:"
  local tasks
  tasks="$(parse_backlog | grep '^TODO|' | sort -t'|' -k3,3n)"

  local count=0
  while IFS='|' read -r status id prio title depends line_num; do
    if deps_satisfied "$depends"; then
      run_task "$id" "$title"
      count=$((count + 1))
    else
      log_info "[DRY RUN] Skipped $id (depends: $depends not satisfied)"
    fi
  done <<< "$tasks"

  if (( count == 0 )); then
    log_info "[DRY RUN] No tasks ready to run."
  fi
}

main_loop() {
  setup
  recover_interrupted
  show_status

  while true; do
    if ! acquire_lock; then
      sleep 10
      continue
    fi

    trap 'release_lock; exit' INT TERM EXIT

    local task_info
    if task_info="$(next_task)"; then
      IFS='|' read -r id prio title depends line_num <<< "$task_info"
      run_task "$id" "$title" || true
    else
      log_info "No tasks ready. Waiting ${RUNNER_INTERVAL}s..."
    fi

    release_lock
    trap - INT TERM EXIT

    if $SINGLE_RUN; then
      log_info "Single run mode. Exiting."
      break
    fi

    show_status
    sleep "$RUNNER_INTERVAL"
  done
}

# ---- CLI ----

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Claude Code Backlog Runner — processes tasks from BACKLOG.md

Options:
  --dry-run       Parse and show tasks without executing
  --single        Run one task and exit
  --status        Show backlog status and exit
  --worker-id ID  Set worker identifier (default: worker-1)
  -h, --help      Show this help

Environment (via .env):
  PROJECT_DIR       Project directory for Claude to work in
  RUNNER_INTERVAL   Seconds between task checks (default: 30)
  MAX_RETRIES       Max retry attempts per task (default: 3)
  LOG_LEVEL         Logging level: info, debug (default: info)
  WORKER_ID         Worker identifier (default: worker-1)
EOF
}

# Parse CLI args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --single)    SINGLE_RUN=true; shift ;;
    --status)    setup; show_status; exit 0 ;;
    --worker-id) WORKER_ID="$2"; shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

log_info "Starting backlog runner (worker: $WORKER_ID, project: $PROJECT_DIR)"

if $DRY_RUN; then
  run_dry
else
  main_loop
fi
