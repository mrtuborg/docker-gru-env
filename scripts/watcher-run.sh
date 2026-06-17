#!/usr/bin/env bash
# watcher-run.sh — Run Copilot CLI autopilot through a backlog of GitHub issues.
#
# Each issue gets its own `gh copilot -p` invocation (= separate session).
# The sessionEnd hook fires after each → cost attributed to the issue in
# ~/.copilot/cost-log.jsonl.
#
# Usage:
#   ./scripts/watcher-run.sh --config .gru/config.yml [--dry-run]
#   ./scripts/watcher-run.sh --repo owner/repo --project N [--host github.com]
#   ./scripts/watcher-run.sh --repo owner/repo --project N --dry-run
#   ./scripts/watcher-run.sh --repo owner/repo --project N --log-dir ~/watcher-logs
#   ./scripts/watcher-run.sh --repo owner/repo --project N --max-per-issue 5
#   ./scripts/watcher-run.sh --config ... --resume          # continue a previous run
#   ./scripts/watcher-run.sh --config ... --resume --state-file /path/to/state.json
#
# Stage behaviour:
#   Determined by prompt files in stage-prompts/{Stage}.md.
#   Built-in handlers: Todo, In Progress.
#   Issues in stages with no matching handler file get a comment and are skipped.
#   Consumer projects override by setting watcher.prompts_dir in config.
# Log files written when --log-dir is set:
#   <log-dir>/run-<date>.log          — full script output (all issues)
#   <log-dir>/issue-<N>-<date>.log    — per-issue session output
#
# Requirements:
#   - gh CLI authenticated (GH_HOST set for GHE)
#   - ~/.copilot/hooks/hooks.json with sessionEnd hook
#   - issue-start and session-handoff skills installed

set -euo pipefail

# Re-exec from a temp copy so that in-place edits to this script (e.g. a git
# commit mid-run) don't corrupt the running instance.  Bash reads scripts
# lazily from the file descriptor; if the file changes, bash starts reading
# new/shifted content mid-loop and can execute Python/heredoc text as shell
# commands.  Taking a snapshot at startup prevents that entirely.
if [[ -z "${_OVERNIGHT_REEXEC:-}" ]]; then
  # macOS mktemp requires X's at the END of the template — no .sh suffix allowed.
  _tmp=$(mktemp /tmp/watcher-run.XXXXXX)
  cp "$0" "$_tmp"
  chmod +x "$_tmp"
  export _OVERNIGHT_REEXEC=1
  # Pass the original script's directory so SCRIPT_DIR stays correct after
  # re-exec (the temp copy lives in /tmp, which would break relative paths).
  export _OVERNIGHT_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  # Preserve original script path for use in user-facing messages (temp copy
  # is deleted on exit, so $0 would point at a gone file).
  export _OVERNIGHT_ORIG_SCRIPT="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  exec bash "$_tmp" "$@"
fi
# Clean up the temp snapshot when the re-exec'd copy exits.
[[ -n "${_OVERNIGHT_REEXEC:-}" ]] && trap 'rm -f "$0"' EXIT

# Use the original script location (set before re-exec) when available so that
# paths like $SCRIPT_DIR/../src/ resolve correctly even from the /tmp copy.
if [[ -n "${_OVERNIGHT_SCRIPT_DIR:-}" ]]; then
  SCRIPT_DIR="$_OVERNIGHT_SCRIPT_DIR"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
fi

# Never invoke a pager in automation — gh defaults to `less` on a TTY
export GH_PAGER=cat

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
REPO=""
PROJECT_NUM=""
GH_HOST="${GH_HOST:-github.com}"
DRY_RUN=false
MAX_ISSUES=50   # safety cap
MAX_PER_ISSUE=3 # max sessions per issue per run (prevents retry storms)
POLL_INTERVAL=300 # seconds to sleep when no actionable issues found (0 = run once)
_CLI_POLL_INTERVAL=""  # set to 1 when --poll-interval is given; prevents config overwrite
LOG_DIR=""
PROJECT_OWNER=""  # org/user owning the project board (defaults to repo owner)
CONFIG_PATH=""
CONSUMER_PROMPTS_DIR=""  # resolved after config load
WORKING_DIR=""           # directory to run sessions from (defaults to cwd)
RESUME=false             # --resume: load state file and skip already-completed issues
STATE_FILE=""            # explicit path to state file (auto-derived when empty)
MODEL=""                 # --model: Copilot model override (also set via watcher.model in config)
MODELS_LIST=()           # ordered list of models to try (priority 1 first, cheapest last)
CURRENT_MODEL_IDX=0      # index into MODELS_LIST currently in use
MODEL_CONSEC_FAIL=0      # consecutive session failures on current model; triggers fallback at 3

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)           REPO="$2";         shift 2 ;;
    --project)        PROJECT_NUM="$2";  shift 2 ;;
    --project-owner)  PROJECT_OWNER="$2"; shift 2 ;;
    --host)           GH_HOST="$2";      shift 2 ;;
    --config)         CONFIG_PATH="$2";  shift 2 ;;
    --dry-run)        DRY_RUN=true;      shift   ;;
    --max)            MAX_ISSUES="$2";    shift 2 ;;
    --max-per-issue)  MAX_PER_ISSUE="$2"; shift 2 ;;
    --poll-interval)  POLL_INTERVAL="$2"; _CLI_POLL_INTERVAL=1; shift 2 ;;
    --log-dir)        LOG_DIR="$2";      shift 2 ;;
    --working-dir)    WORKING_DIR="$2";  shift 2 ;;
    --workspace-dir)  WORKING_DIR="$2";  shift 2 ;;  # alias used by docker/entrypoint.sh
    --resume)         RESUME=true;       shift   ;;
    --state-file)     STATE_FILE="$2";   shift 2 ;;
    --model)          MODEL="$2";        shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Load config file defaults (explicit CLI args take precedence)
# ---------------------------------------------------------------------------
if [[ -n "$CONFIG_PATH" ]]; then
  if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "ERROR: config file not found: $CONFIG_PATH"
    exit 1
  fi
  # Load required fields; fail loudly if config is invalid
  _cfg_load() {
    local key="$1"
    local val
    val=$(python3 "$SCRIPT_DIR/../src/workflow_config.py" --config "$CONFIG_PATH" --get "$key" 2>&1) || {
      echo "ERROR: failed to read config key '$key' from $CONFIG_PATH: $val" >&2
      exit 1
    }
    echo "$val"
  }
  # _cfg_optional silently returns empty if key is missing
  _cfg_optional() {
    python3 "$SCRIPT_DIR/../src/workflow_config.py" --config "$CONFIG_PATH" --get "$1" 2>/dev/null || true
  }

  [[ -z "$REPO" ]]          && REPO=$(_cfg_load data_repo)
  [[ -z "$PROJECT_NUM" ]]   && PROJECT_NUM=$(_cfg_load project.number)
  [[ -z "$PROJECT_OWNER" ]] && PROJECT_OWNER=$(_cfg_load project.owner)
  if [[ "$GH_HOST" == "github.com" ]]; then
    v=$(_cfg_optional gh_host)
    [[ -n "$v" ]] && GH_HOST="$v"
  fi
  # Load working_dir FIRST so prompts_dir can be resolved relative to it.
  _wdir=$(_cfg_optional working_dir)
  [[ -z "$WORKING_DIR" && -n "$_wdir" ]] && WORKING_DIR="$_wdir"
  _tpl=$(_cfg_optional watcher.prompts_dir)
  if [[ -n "$_tpl" ]]; then
    # Relative paths are resolved against WORKING_DIR (the consumer repo checkout,
    # /workspace in the container). This means the consumer repo can put its stage
    # prompts at e.g. ".gru/stage-prompts/" (relative to its root)
    # and the container will find them at /workspace/.gru/stage-prompts/.
    if [[ "$_tpl" != /* && -n "${WORKING_DIR:-}" ]]; then
      CONSUMER_PROMPTS_DIR="${WORKING_DIR%/}/${_tpl}"
    else
      CONSUMER_PROMPTS_DIR="$_tpl"
    fi
  fi
  # Load stage_order as pipe-separated list. Pipe delimiter avoids splitting on spaces
  # in stage names like "In progress". Python list → join with | → split on | when used.
  _stage_order_raw=$(_cfg_optional watcher.stage_order)
  if [[ -n "$_stage_order_raw" ]]; then
    STAGE_ORDER=$(echo "$_stage_order_raw" | python3 -c "
import sys, ast
v = sys.stdin.read().strip()
try:
    lst = ast.literal_eval(v)
    print('|'.join(str(x) for x in lst))
except Exception:
    # already pipe-separated plain string
    print(v)
")
  fi
  unset _tpl _wdir _stage_order_raw
  _pi=$(_cfg_optional watcher.poll_interval)
  [[ -n "$_pi" && "$_pi" =~ ^[0-9]+$ && -z "$_CLI_POLL_INTERVAL" ]] && POLL_INTERVAL="$_pi"
  unset _pi
  # Load allowed_repos as space-separated string; default to data_repo if not set.
  _allowed_repos_raw=$(_cfg_optional allowed_repos)
  if [[ -n "$_allowed_repos_raw" ]]; then
    ALLOWED_REPOS=$(echo "$_allowed_repos_raw" | python3 -c "
import sys, ast
v = sys.stdin.read().strip()
try:
    lst = ast.literal_eval(v)
    print(' '.join(str(x) for x in lst))
except Exception:
    print(v)
")
  fi
  unset _allowed_repos_raw
  # Load watcher.models (list of {model, priority} items) — enables model fallback.
  # Falls back to watcher.model (single string) for backward compat.
  _raw_models=$(python3 - "$CONFIG_PATH" << 'PYEOF' 2>/dev/null
import sys, yaml
try:
    conf = yaml.safe_load(open(sys.argv[1]))
    models = conf.get('watcher', {}).get('models', [])
    if models:
        models.sort(key=lambda m: m.get('priority', 99) if isinstance(m, dict) else 99)
        for m in models:
            name = m.get('model', '') if isinstance(m, dict) else str(m)
            if name:
                print(name)
except Exception:
    pass
PYEOF
  )
  if [[ -n "$_raw_models" ]]; then
    while IFS= read -r _m; do MODELS_LIST+=("$_m"); done <<< "$_raw_models"
  fi
  unset _raw_models _m
  # Singular watcher.model is still supported when models list is absent.
  if [[ ${#MODELS_LIST[@]} -eq 0 ]]; then
    _model=$(_cfg_optional watcher.model || true)
    [[ -n "${_model:-}" ]] && MODEL="$_model" && MODELS_LIST=("$_model")
    unset _model
  fi
fi

# If --model was passed on CLI, it seeds the list (overrides config; no fallback).
[[ -n "${MODEL:-}" && ${#MODELS_LIST[@]} -eq 0 ]] && MODELS_LIST=("$MODEL")
# Set active model from list position 0 (re-set at runtime if fallback triggers).
[[ ${#MODELS_LIST[@]} -gt 0 ]] && MODEL="${MODELS_LIST[0]}"

# Default STAGE_ORDER if not set from config (built-in: Todo, In progress)
STAGE_ORDER="${STAGE_ORDER:-Todo|In progress}"
# ALLOWED_REPOS: space-separated list of owner/repo pairs whose issues this board may process.
# Always includes REPO (data_repo) regardless of config — union at runtime.
ALLOWED_REPOS="${ALLOWED_REPOS:-}"
printf '%s\n' $ALLOWED_REPOS | grep -qxF "$REPO" \
  || ALLOWED_REPOS="${REPO}${ALLOWED_REPOS:+ $ALLOWED_REPOS}"

# Built-in stage handlers live here; consumer dir overlays on top.
BUILTIN_STAGES_DIR="$(dirname "$SCRIPT_DIR")/stage-prompts"

if ! command -v envsubst >/dev/null 2>&1; then
  echo "ERROR: envsubst is required but not found (install gettext)"
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage handler resolution
# ---------------------------------------------------------------------------

# Returns the resolved prompt file path for a stage, or empty if none exists.
_resolve_stage_prompt() {
  local stage="$1"
  if [[ -n "$CONSUMER_PROMPTS_DIR" && -r "${CONSUMER_PROMPTS_DIR}/${stage}.md" ]]; then
    echo "${CONSUMER_PROMPTS_DIR}/${stage}.md"
  elif [[ -r "${BUILTIN_STAGES_DIR}/${stage}.md" ]]; then
    echo "${BUILTIN_STAGES_DIR}/${stage}.md"
  else
    echo ""
  fi
}

# Posts a "no handler" comment on an issue (idempotent — skips if already posted).
_post_no_handler_comment() {
  local issue_num="$1" stage="$2"
  local already_posted
  already_posted=$(GH_HOST="$GH_HOST" gh issue view "$issue_num" --repo "$REPO" \
    --json comments \
    --jq '[.comments[].body | select(contains("watcher-run: no-handler"))] | length' \
    2>/dev/null || echo "0")
  if [[ "$already_posted" == "0" ]]; then
    GH_HOST="$GH_HOST" gh issue comment "$issue_num" --repo "$REPO" --body \
      "<!-- watcher-run: no-handler -->⏸ **watcher-run**: Stage \`${stage}\` has no automation handler — this issue awaits human action." \
      2>/dev/null || true
    echo "  → posted 'no handler' comment on #${issue_num}"
  else
    echo "  → already notified (no handler for '${stage}')"
  fi
}

if [[ -z "$REPO" || -z "$PROJECT_NUM" ]]; then
  echo "Usage: $0 --config PATH [--dry-run] [--poll-interval SECS]"
  echo "       $0 --repo owner/repo --project N [--project-owner ORG] [--host HOST] [--log-dir DIR] [--dry-run]"
  echo "       $0 ... --poll-interval SECS   # sleep between board polls when idle (0=run once, default=300)"
  echo "       $0 ... --resume [--state-file PATH]   # continue a previous run"
  exit 1
fi

# ORG is used for project GraphQL queries; may differ from the repo owner.
ORG="${PROJECT_OWNER:-$(echo "$REPO" | cut -d/ -f1)}"
DATE_TAG=$(date +%Y%m%d-%H%M%S)
CURRENT_ISSUE=""

# ---------------------------------------------------------------------------
# State file — persists completed issue numbers and attempt counts across runs.
# Written after every successful issue; read on --resume to skip already-done
# issues and restore attempt counters (so per-issue caps carry over).
# ---------------------------------------------------------------------------
_safe_repo=$(echo "$REPO" | tr '/' '_')
if [[ -z "$STATE_FILE" ]]; then
  _state_base="watcher-state-${_safe_repo}-proj${PROJECT_NUM}.json"
  if [[ -n "$LOG_DIR" ]]; then
    STATE_FILE="$LOG_DIR/${_state_base}"
  else
    STATE_FILE="/tmp/${_state_base}"
  fi
  unset _state_base
fi
unset _safe_repo

# _state_write: update state file with current completed/attempt data.
# Args: $1 = completed token "REPO:ISSUE_NUM:STAGE" (empty string = flush attempts only)
_state_write() {
  local new_token="${1:-}"
  # Collect current attempt variables into "KEY=VALUE ..." string
  local _attempt_dump
  _attempt_dump=$(set | grep '^ISSUE_ATTEMPTS__' | tr '\n' ' ' || true)
  python3 - "$new_token" "$STATE_FILE" "$_attempt_dump" <<'PYEOF'
import json, sys, tempfile, os
new_token = sys.argv[1]        # "repo:issue:stage" or ""
state_file = sys.argv[2]
attempt_str = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    with open(state_file) as f:
        state = json.load(f)
except Exception:
    state = {"completed": [], "attempts": {}}
if new_token and new_token not in state["completed"]:
    state["completed"].append(new_token)
# Restore attempt counts from env dump (KEY=VALUE pairs)
for token in attempt_str.split():
    if "=" in token:
        k, _, v = token.partition("=")
        if k.startswith("ISSUE_ATTEMPTS__"):
            try:
                state["attempts"][k] = int(v)
            except ValueError:
                pass
# Write atomically via a sibling temp file + rename.
dirpath = os.path.dirname(os.path.abspath(state_file))
fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".json.tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, state_file)
except Exception:
    os.unlink(tmp_path)
    raise
PYEOF
}

# _state_load: restore completed list and attempt counts from state file.
# Sets RESUMED_COMPLETED (space-separated "REPO:ISSUE:STAGE" tokens) and
# re-evaluates ISSUE_ATTEMPTS__* variables so retry caps carry over.
_state_load() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo "  State file not found: $STATE_FILE"
    RESUMED_COMPLETED=""
    return
  fi
  local _out
  _out=$(python3 - "$STATE_FILE" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        state = json.load(f)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(0)
completed = " ".join(str(t) for t in state.get("completed", []))
print(f"COMPLETED={completed}")
for k, v in state.get("attempts", {}).items():
    if k.startswith("ISSUE_ATTEMPTS__"):
        print(f"{k}={v}")
PYEOF
) || true
  RESUMED_COMPLETED=""
  while IFS= read -r _line; do
    [[ -z "$_line" ]] && continue
    case "$_line" in
      COMPLETED=*) RESUMED_COMPLETED="${_line#COMPLETED=}" ;;
      ISSUE_ATTEMPTS__*)
        _k="${_line%%=*}"
        _v="${_line#*=}"
        # Accept only integer values — reject any injection attempt
        [[ "$_v" =~ ^[0-9]+$ ]] && printf -v "$_k" '%s' "$_v"
        ;;
    esac
  done <<< "$_out"
  unset _out _line
}

# _state_is_done: return 0 (true) if REPO:ISSUE_NUM:STAGE is in RESUMED_COMPLETED.
_state_is_done() {
  local _tok="$1"
  [[ " $RESUMED_COMPLETED " == *" $_tok "* ]]
}

RESUMED_COMPLETED=""
if $RESUME; then
  echo "📂 Resuming — loading state from: $STATE_FILE"
  _state_load
  if [[ -n "$RESUMED_COMPLETED" ]]; then
    # Show as "#N (stage)" for readability; tokens are "repo:N:stage"
    _pretty=$(echo "$RESUMED_COMPLETED" | tr ' ' '\n' \
      | awk -F: '{print "#"$2" ["$3"]"}' | paste -sd ', ' -)
    echo "   Already completed in previous run(s): ${_pretty}"
    unset _pretty
  else
    echo "   No previously completed sessions found in state file."
  fi
  echo ""
fi

# Resolve the directory sessions will run from.
# When working_dir is set (config or --working-dir), sessions run from there and cost-sync
# receives --repo explicitly. When not set, sessions inherit cwd (legacy behaviour) and
# cost attribution is derived from the git remote at cwd.
if [[ -n "$WORKING_DIR" ]]; then
  SESSION_DIR="$(cd "$WORKING_DIR" && pwd)"
  echo "Working dir: $SESSION_DIR  (sessions will run here)"
  echo "Repo:        $REPO  (passed explicitly to cost-sync)"
else
  # Legacy: verify cwd is the target repo — sessions inherit cwd for cost attribution.
  ACTUAL_REPO=$(git remote get-url origin 2>/dev/null \
    | sed -E 's|.*[:/]([^/]+/[^/]+)$|\1|' \
    | sed 's|\.git$||')
  if [[ "$ACTUAL_REPO" != "$REPO" ]]; then
    echo "ERROR: current directory is not the target repo."
    echo "  Expected: $REPO"
    echo "  Got:      ${ACTUAL_REPO:-<not a git repo>}"
    echo ""
    echo "Fix: either cd into the local clone of $REPO first:"
    echo "  cd /path/to/$(echo "$REPO" | cut -d/ -f2)"
    echo "  GH_HOST=$GH_HOST $0 --repo $REPO --project $PROJECT_NUM ..."
    echo ""
    echo "Or set working_dir in your config to run from any directory:"
    echo "  working_dir: /path/to/workspace  # absolute or relative to config file"
    exit 1
  fi
  SESSION_DIR="$(pwd)"
  echo "Repo check OK: $ACTUAL_REPO"
fi

# ---------------------------------------------------------------------------
# _finalize_session: write issue-refs sidecar, run cost-sync + cost-board-sync.
# Called after every gh copilot session exits — normal completion, failure,
# or interrupt.  Idempotent: skips sidecar if already written by the session.
#
# Globals read: SCRIPT_DIR, REPO, WORKING_DIR, ORG, PROJECT_NUM, GH_HOST
# Args:
#   $1  sessions_before  — output of `ls ~/.copilot/session-state/ | sort` before launch
#   $2  issue_num        — issue number
#   $3  issue_repo       — "owner/repo"
#   $4  issue_stage      — stage name (for written_by attribution only)
# ---------------------------------------------------------------------------
_finalize_session() {
  local sessions_before="$1"
  local issue_num="$2"
  local issue_repo="$3"
  local issue_stage="$4"

  local sessions_after new_session_id
  sessions_after=$(ls ~/.copilot/session-state/ 2>/dev/null | sort || true)
  new_session_id=$(comm -13 <(echo "$sessions_before") <(echo "$sessions_after") \
    | head -1 || true)

  if [[ -z "$new_session_id" ]]; then
    echo "  WARNING: could not identify new session ID — cost may not be attributed"
    return
  fi

  echo "  Session ID: $new_session_id"

  # Write issue-refs.json sidecar so cost-sync can attribute tokens to the
  # correct issue even when issue-start skill didn't write it inside the session.
  local sidecar_dir="$HOME/.copilot/session-state/$new_session_id"
  local sidecar_path="$sidecar_dir/issue-refs.json"
  if [[ -d "$sidecar_dir" && ! -f "$sidecar_path" ]]; then
    local issue_api_id
    issue_api_id=$(GH_HOST="$GH_HOST" \
      gh api "repos/${issue_repo}/issues/${issue_num}" \
      --jq '.id' 2>/dev/null || echo "")
    python3 - "$sidecar_path" "$issue_num" "${issue_api_id:-}" \
              "$issue_repo" "$issue_stage" <<'PYEOF' \
      2>/dev/null || echo "  WARNING: could not write issue-refs.json sidecar"
import json, sys, datetime
path, num, api_id, repo, stage = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
sidecar = {
    "issue_number": num,
    "issue_api_id": int(api_id) if api_id else None,
    "confidence": "exact",
    "activated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "repo": repo,
    "written_by": f"watcher-run.sh [{stage}] fallback",
}
with open(path, "w") as f:
    json.dump(sidecar, f, indent=2)
print(f"  issue-refs.json written: {path}")
PYEOF
  fi

  # cost-sync: import token usage for this session into cost-log.jsonl.
  local cost_sync_repo_flag="${REPO:+--repo $REPO}"
  [[ -n "$WORKING_DIR" && -n "$issue_repo" ]] && \
    cost_sync_repo_flag="--force-repo $issue_repo"
  echo "  Running cost-sync…"
  # shellcheck disable=SC2086
  COPILOT_SESSION_ID="$new_session_id" \
    python3 "$SCRIPT_DIR/../src/cost-sync.py" \
    $cost_sync_repo_flag \
    --project-hint "$PROJECT_NUM" \
    2>/dev/null || echo "  WARNING: cost-sync failed (non-fatal)"

  # cost-board-sync: update "Cost ($)" field on the project board.
  # Use issue_repo (not REPO) when working_dir is set — cost-sync attributes the
  # session to issue_repo, so the repo filter here must match that value.
  local board_sync_repo="${issue_repo:-$REPO}"
  echo "  Syncing cost for issue #${issue_num} to project board…"
  GH_HOST="$GH_HOST" python3 "$SCRIPT_DIR/../src/cost-board-sync.py" \
    --project-owner "$ORG" \
    --all-projects \
    --repo "$board_sync_repo" \
    --gh-host "$GH_HOST" \
    --issue "$issue_num" \
    2>/dev/null || echo "  WARNING: cost-board-sync failed for #${issue_num} (non-fatal)"
}

# Globals set just before each session launch so _on_interrupt can call
# _finalize_session even when the session was killed mid-flight.
_PRE_SESSION_SNAPSHOT=""
_PRE_SESSION_ISSUE_REPO=""
_PRE_SESSION_ISSUE_STAGE=""

# ---------------------------------------------------------------------------
# Interrupt trap
# ---------------------------------------------------------------------------
STARTING_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

_on_interrupt() {
  echo ""
  echo "⚠  Interrupted!"
  if [[ -n "$CURRENT_ISSUE" && -n "$_PRE_SESSION_SNAPSHOT" ]]; then
    echo "   Issue #$CURRENT_ISSUE was in progress."
    echo "   Before restarting, check:"
    echo "     git status"
    echo "     git stash   # if dirty"
    echo "     git checkout $STARTING_BRANCH && git pull"
    echo ""
    # Allow the killed session process a moment to flush its session-state dir.
    echo "   Waiting for session state to flush (2 s)…"
    sleep 2
    echo "   Running best-effort cost attribution for interrupted session…"
    _finalize_session \
      "$_PRE_SESSION_SNAPSHOT" \
      "$CURRENT_ISSUE" \
      "$_PRE_SESSION_ISSUE_REPO" \
      "$_PRE_SESSION_ISSUE_STAGE"
    # Release watcher-lock so the issue isn't permanently stuck.
    [[ -n "$CURRENT_ISSUE" && -n "$_PRE_SESSION_ISSUE_REPO" ]] && \
      GH_HOST="$GH_HOST" gh issue edit "$CURRENT_ISSUE" \
        --repo "$_PRE_SESSION_ISSUE_REPO" \
        --remove-label "watcher-lock" 2>/dev/null || true
  fi
  # Flush current attempt counts to state file so retry caps survive the restart.
  if _state_write "" 2>/dev/null; then
    echo ""
    echo "State saved to: $STATE_FILE"
  else
    echo ""
    echo "WARNING: could not save state to: $STATE_FILE"
  fi
  echo ""
  echo "To resume (skip already-completed sessions and restore retry caps):"
  # Use the original script path (before re-exec to temp copy, which is deleted on exit).
  _orig="${_OVERNIGHT_ORIG_SCRIPT:-$SCRIPT_DIR/$(basename "$0")}"
  _resume_cmd="$(printf '%q' "$_orig")"
  [[ -n "$CONFIG_PATH" ]] && _resume_cmd+=" --config $(printf '%q' "$CONFIG_PATH")"
  [[ -z "$CONFIG_PATH" ]] && _resume_cmd+=" --repo $(printf '%q' "$REPO") --project $(printf '%q' "$PROJECT_NUM")"
  [[ "$GH_HOST" != "github.com" ]] && _resume_cmd+=" --host $(printf '%q' "$GH_HOST")"
  [[ -n "$LOG_DIR" ]] && _resume_cmd+=" --log-dir $(printf '%q' "$LOG_DIR")"
  [[ -n "$WORKING_DIR" ]] && _resume_cmd+=" --working-dir $(printf '%q' "$WORKING_DIR")"
  _resume_cmd+=" --resume --state-file $(printf '%q' "$STATE_FILE")"
  echo "  $_resume_cmd"
  echo ""
  echo "(Without --resume: already-started issues are still skipped if issue-start"
  echo " moved them to a different stage — but retry caps will reset.)"
  unset _resume_cmd _orig
  exit 130
}
trap _on_interrupt INT TERM

# ---------------------------------------------------------------------------
# Mirror ~/.copilot session data → COPILOT_DATA_HOME (persistent data volume)
# gh copilot writes session-store.db and session-state/ to ~/.copilot/ (hardcoded).
# cost-sync.py reads from $COPILOT_DATA_HOME. Symlink them so both paths are the same.
# ---------------------------------------------------------------------------
if [[ -n "${COPILOT_DATA_HOME:-}" && "$COPILOT_DATA_HOME" != "$HOME/.copilot" ]]; then
  mkdir -p "$COPILOT_DATA_HOME"
  for _item in session-store.db session-state; do
    _src="$HOME/.copilot/$_item"
    _dst="$COPILOT_DATA_HOME/$_item"
    if [[ -L "$_src" ]]; then
      : # already a symlink — leave it
    elif [[ -e "$_src" && ! -L "$_src" ]]; then
      # Real file/dir exists: move to data volume then symlink
      [[ ! -e "$_dst" ]] && mv "$_src" "$_dst" || rm -rf "$_src"
      ln -sf "$_dst" "$_src"
    else
      # Nothing in home yet: create destination and symlink
      [[ "$_item" == *.db ]] || mkdir -p "$_dst"
      ln -sf "$_dst" "$_src"
    fi
  done
  unset _item _src _dst
fi

# ---------------------------------------------------------------------------
# Set up logging
# ---------------------------------------------------------------------------
if [[ -n "$LOG_DIR" ]]; then
  mkdir -p "$LOG_DIR"
  RUN_LOG="$LOG_DIR/run-${DATE_TAG}.log"
  # Tee all subsequent output to the run log
  exec > >(tee -a "$RUN_LOG") 2>&1
  echo "Logging to: $RUN_LOG"
fi

# ---------------------------------------------------------------------------
# Fetch open issues from GitHub project board
# ---------------------------------------------------------------------------
echo "=== docker-gru-env watcher run ==="
echo "Repo:    $REPO"
echo "Org:     $ORG"
echo "Project: #$PROJECT_NUM  ($GH_HOST)"
echo "Stages:  built-in: $BUILTIN_STAGES_DIR"
[[ -n "$CONSUMER_PROMPTS_DIR" ]] && echo "         consumer: $CONSUMER_PROMPTS_DIR"
echo "Date:    $(date)"
# Warn when project owner differs from repo owner — common misconfiguration.
_repo_owner=$(echo "$REPO" | cut -d/ -f1)
if [[ "$ORG" != "$_repo_owner" ]]; then
  echo "NOTE: project owner '$ORG' differs from repo owner '$_repo_owner' (pass --project-owner to override)"
fi
unset _repo_owner
echo ""

_matched_entity=""
# Detect which GraphQL entity type owns the project (org or user) — done once.
for _entity in organization user; do
  _probe=$(GH_HOST="$GH_HOST" gh api graphql -f query="
  { ${_entity}(login:\"$ORG\") { projectV2(number:$PROJECT_NUM) { id } } }" 2>/dev/null) || continue
  _null=$(echo "$_probe" | jq -r ".data.${_entity}.projectV2 == null" 2>/dev/null) || continue
  [[ "$_null" == "true" ]] && continue
  _matched_entity="$_entity"
  PROJECT_ID=$(echo "$_probe" | jq -r ".data.${_entity}.projectV2.id" 2>/dev/null)
  PROJECT_ENTITY="$_entity"
  break
done

if [[ -z "$_matched_entity" ]]; then
  echo "ERROR: project #$PROJECT_NUM not found under org/user '$ORG' on $GH_HOST"
  exit 1
fi

# Print the resolved project URL so the user can verify the right board was picked.
if [[ "$_matched_entity" == "organization" ]]; then
  echo "Board:   https://${GH_HOST}/orgs/${ORG}/projects/${PROJECT_NUM}"
elif [[ "$_matched_entity" == "user" ]]; then
  echo "Board:   https://${GH_HOST}/users/${ORG}/projects/${PROJECT_NUM}"
fi

# ---------------------------------------------------------------------------
# _query_board: returns "NUMBER STAGE" lines for all open AI-actionable issues.
# Sorted by: stage priority (rightmost in STAGE_ORDER first), then issue number.
# ---------------------------------------------------------------------------
_query_board() {
  local raw
  raw=$(GH_HOST="$GH_HOST" gh api graphql -f query="
  { ${_matched_entity}(login:\"$ORG\") { projectV2(number:$PROJECT_NUM) { items(first:100) {
    nodes {
      content { ... on Issue { number title state repository { nameWithOwner }
                               labels(first:10) { nodes { name } } } }
      fieldValues(first:10) { nodes {
        ... on ProjectV2ItemFieldSingleSelectValue {
          name field { ... on ProjectV2SingleSelectField { name } }
        }
      }}
    }
  } } } }" 2>/dev/null) || { echo "" ; return; }

  # Build priority map from STAGE_ORDER (rightmost = highest priority = lowest sort key)
  # Then emit: <priority> <number> <repo> <stage>  sorted ascending, strip priority column
  # Issues carrying 'watcher-lock' are skipped — another container is processing them.
  echo "$raw" | jq -r "[.data.${_matched_entity}.projectV2.items.nodes[]
    | select(.content.state==\"OPEN\")
    | select((.content.labels.nodes // []) | map(.name) | index(\"watcher-lock\") | not)
    | { number: .content.number, title: .content.title,
        repo: .content.repository.nameWithOwner,
        stage: (.fieldValues.nodes[] | select(.field.name==\"Status\") | .name) }
    | select(.stage != null and .stage != \"\")]
    | sort_by(.number)[] | \"\(.number) \(.repo) \(.stage)\"" 2>/dev/null \
  | python3 -c "
import sys
stages = sys.argv[1].split('|')
prio = {s: len(stages) - 1 - i for i, s in enumerate(stages)}
lines = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    parts = line.split(None, 2)
    if len(parts) < 3: continue
    num, repo, stage = int(parts[0]), parts[1], parts[2]
    lines.append((prio.get(stage, 999), num, repo, stage))
lines.sort()
for _, num, repo, stage in lines:
    print(f'{num} {repo} {stage}')
" "$STAGE_ORDER"
}

# ---------------------------------------------------------------------------
# _pick_next: query board and return the first actionable issue (rightmost stage).
# Sets globals PICK_NUM, PICK_REPO, and PICK_STAGE. Returns 1 if nothing to do.
# ---------------------------------------------------------------------------
_pick_next() {
  local all_issues
  all_issues=$(_query_board)
  [[ -z "$all_issues" ]] && return 1

  while IFS=' ' read -r num repo stage; do
    local h
    h=$(_resolve_stage_prompt "$stage")
    [[ -z "$h" ]] && continue
    # In resume mode, skip sessions already completed in a prior run.
    if $RESUME && _state_is_done "${repo}:${num}:${stage}"; then
      continue
    fi
    # Skip issues that have already hit the per-issue retry cap to prevent
    # an infinite loop when a capped issue sorts before an uncapped one.
    local _pn_ak _pn_av
    _pn_ak="ISSUE_ATTEMPTS__$(printf '%s__%s' "$num" "$stage" | tr -c 'a-zA-Z0-9_' '_')"
    eval "_pn_av=\"\${${_pn_ak}:-0}\""
    if [[ "$_pn_av" -ge "$MAX_PER_ISSUE" ]]; then
      continue
    fi
    unset _pn_ak _pn_av
    PICK_NUM="$num"; PICK_REPO="$repo"; PICK_STAGE="$stage"; return 0
  done <<< "$all_issues"

  return 1
}

# ---------------------------------------------------------------------------
# Initial board scan — print what's actionable (for the user to see).
# If POLL_INTERVAL=0 and the board is empty, exit immediately (single-pass).
# Otherwise let the main idle-poll loop handle re-querying.
# ---------------------------------------------------------------------------
_initial_issues=$(_query_board)
if [[ -z "$_initial_issues" ]]; then
  echo "No open issues found on project board."
  if [[ "$POLL_INTERVAL" -eq 0 ]]; then
    echo "Nothing to do (single-pass mode)."
    exit 0
  fi
  echo "Will re-poll every ${POLL_INTERVAL}s — waiting for issues to appear."
fi

echo "Board snapshot:"
SKIPPED_RESUME=0
while IFS=' ' read -r N IREPO STAGE; do
  _h=$(_resolve_stage_prompt "$STAGE")
  if [[ -n "$_h" ]]; then
    if $RESUME && _state_is_done "${IREPO}:${N}:${STAGE}"; then
      echo "  #$N  [$STAGE]  ($IREPO)  → already completed in a previous run (will skip)"
      SKIPPED_RESUME=$((SKIPPED_RESUME + 1))
    else
      echo "  #$N  [$STAGE]  ($IREPO)  → handler: $_h"
    fi
  else
    echo "  #$N  [$STAGE]  ($IREPO)  → no handler (human-managed, will skip)"
  fi
done <<< "$_initial_issues"
echo ""

if $DRY_RUN; then
  echo "Dry run — exiting without starting sessions."
  exit 0
fi

# ---------------------------------------------------------------------------
# Pull-principle main loop: re-query after every session, always pick the
# rightmost AI-actionable stage. Stops when no actionable issues remain.
# ---------------------------------------------------------------------------
DONE=0
FAILED=0
SKIPPED=0
PICK_NUM=""
PICK_REPO=""
PICK_STAGE=""
RUN_START_TS=$(date +%s)
ISSUE_START_TS=0   # set just before each session; used for per-issue duration in summary

# ---------------------------------------------------------------------------
# Per-issue summary and failure helpers (write to LOG_DIR on the host)
# ---------------------------------------------------------------------------

# Append one row to run-summary.md after each session.
_summary_append_row() {
  local num="$1" stage="$2" exit_code="$3" model="${4:-}" start_ts="$5"
  [[ -z "$LOG_DIR" ]] && return
  local summary_file="$LOG_DIR/run-summary.md"
  local ts icon duration first_error=""
  ts=$(date -u '+%Y-%m-%d %H:%M UTC')
  local elapsed=$(( $(date +%s) - start_ts ))
  duration=$(printf "%dm%ds" "$(( elapsed / 60 ))" "$(( elapsed % 60 ))")
  if [[ "$exit_code" -eq 0 ]]; then icon="✅"; elif [[ "$exit_code" -eq 124 ]]; then icon="⏱ timeout"; else icon="❌"; fi

  # Extract first meaningful error line from the per-issue log.
  local issue_log="$LOG_DIR/issue-${num}-${DATE_TAG}.log"
  if [[ "$exit_code" -ne 0 && -f "$issue_log" ]]; then
    first_error=$(grep -iEm1 "(CAPIError|model_not_supported|SESSION TIMEOUT|Execution failed|error.*400|FAIL[^S])" \
      "$issue_log" 2>/dev/null | sed 's/^[[:space:]]*//' | cut -c1-100 || true)
    [[ -z "$first_error" ]] && first_error=$(tail -3 "$issue_log" | tr '\n' ' ' | cut -c1-100)
  fi

  # Write header only once.
  if [[ ! -f "$summary_file" ]]; then
    printf '# Watcher Run Summary — %s\n\n' "$(date -u '+%Y-%m-%d')" > "$summary_file"
    printf '| Time (UTC) | Issue | Stage | Result | Model | Duration | Error |\n' >> "$summary_file"
    printf '|---|---|---|---|---|---|---|\n' >> "$summary_file"
  fi

  printf '| %s | #%s | %s | %s | %s | %s | %s |\n' \
    "$ts" "$num" "$stage" "$icon" "${model:-—}" "$duration" "${first_error:-—}" >> "$summary_file"
}

# Append failure detail to failures.md when a session ends with non-zero exit.
_failures_append() {
  local num="$1" stage="$2" exit_code="$3"
  [[ -z "$LOG_DIR" || "$exit_code" -eq 0 ]] && return
  local fail_file="$LOG_DIR/failures.md"
  local issue_log="$LOG_DIR/issue-${num}-${DATE_TAG}.log"
  {
    printf '## ❌ Issue #%s [%s] — %s\n\n' "$num" "$stage" "$(date -u '+%Y-%m-%d %H:%M UTC')"
    if [[ -f "$issue_log" ]]; then
      local err_lines
      err_lines=$(grep -iE "(CAPIError|model_not_supported|SESSION TIMEOUT|Execution failed|error.*400|FAIL[^S])" \
        "$issue_log" 2>/dev/null | grep -v "^[[:space:]]*[│└●]" | head -10 || true)
      if [[ -n "$err_lines" ]]; then
        printf '**Extracted errors:**\n```\n%s\n```\n\n' "$err_lines"
      fi
      printf '**Last 20 lines of session log:**\n```\n'
      tail -20 "$issue_log"
      printf '```\n\n'
    else
      printf '_No per-issue log found._\n\n'
    fi
    printf '**Full log:** `%s`\n\n---\n\n' "${issue_log}"
  } >> "$fail_file"
}

# Count actionable (handler-present, not resume-skipped) issues for progress display.
_TOTAL_ACTIONABLE=0
while IFS=' ' read -r _tn _tr _ts; do
  [[ -z "$_tn" ]] && continue
  [[ -z "$(_resolve_stage_prompt "$_ts")" ]] && continue
  $RESUME && _state_is_done "${_tr}:${_tn}:${_ts}" && continue
  _TOTAL_ACTIONABLE=$((_TOTAL_ACTIONABLE + 1))
done <<< "$_initial_issues"
unset _tn _tr _ts _initial_issues

# tracks attempt count per issue this run (bash 3.2-compatible: no declare -A)
# keys stored as ISSUE_ATTEMPTS__<issue>__<stage> (colons/slashes sanitised to _)

# Outer idle-poll loop: when the board has no actionable issues, sleep for
# POLL_INTERVAL seconds then re-query. When POLL_INTERVAL=0, exit immediately
# (single-pass mode). Between issues where work IS found, no sleep is added —
# the next _pick_next fires immediately after the session completes.
while true; do
  if ! _pick_next; then
    if [[ "$POLL_INTERVAL" -eq 0 ]]; then
      break   # single-pass mode: exit when board is empty
    fi
    echo "⏸  No actionable issues found — polling again in ${POLL_INTERVAL}s  ($(date '+%H:%M:%S'))"
    sleep "$POLL_INTERVAL"
    continue
  fi

  ISSUE_NUM="$PICK_NUM"
  ISSUE_REPO="$PICK_REPO"
  ISSUE_STAGE="$PICK_STAGE"
  CURRENT_ISSUE="$ISSUE_NUM"

  # Safety net: per-issue retry cap. Normally _pick_next already skips capped
  # issues; this block guards against races or future refactors that bypass that.
  # Key is "ISSUE_NUM:STAGE" so legitimate stage advances don't consume the budget.
  _attempt_key="ISSUE_ATTEMPTS__$(printf '%s__%s' "$ISSUE_NUM" "$ISSUE_STAGE" | tr -c 'a-zA-Z0-9_' '_')"
  eval "_attempts=\"\${${_attempt_key}:-0}\""
  if [[ "$_attempts" -ge "$MAX_PER_ISSUE" ]]; then
    echo "⚠  Issue #$ISSUE_NUM [$ISSUE_STAGE] skipped — already attempted ${_attempts}x this run (max-per-issue=$MAX_PER_ISSUE)"
    # Show the last comment on the issue so the human can diagnose without opening GH.
    _last_comment=$(GH_HOST="$GH_HOST" gh issue view "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --json comments \
      --jq '.comments | last | "  Last comment by \(.author.login // "unknown") at \(.createdAt): \(.body | gsub("\n";" ") | .[0:200])"' \
      2>/dev/null || echo "")
    [[ -n "$_last_comment" ]] && echo "$_last_comment"
    # Post a comment so the issue is visibly flagged for human review.
    _skip_comment="<!-- watcher-run: retry-cap -->⚠️ **watcher-run**: Issue #${ISSUE_NUM} was attempted ${_attempts} times in stage \`${ISSUE_STAGE}\` this run without advancing — skipping to prevent a retry storm. Please investigate manually."
    _already=$(GH_HOST="$GH_HOST" gh issue view "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --json comments \
      --jq '[.comments[].body | select(contains("watcher-run: retry-cap"))] | length' \
      2>/dev/null || echo "0")
    if [[ "$_already" == "0" ]]; then
      GH_HOST="$GH_HOST" gh issue comment "$ISSUE_NUM" --repo "$ISSUE_REPO" \
        --body "$_skip_comment" 2>/dev/null || true
    fi
    unset _attempts _attempt_key _skip_comment _already _last_comment
    SKIPPED=$((SKIPPED + 1))
    CURRENT_ISSUE=""
    # Keep looping — there may be other actionable issues.
    # But to avoid infinite loops when ALL remaining issues are capped, break if
    # we haven't made progress in a full board scan.
    # NOTE: _remaining must NOT use a pipeline subshell — ISSUE_ATTEMPTS__* variables
    # are set in the parent shell and are invisible inside a child process pipeline.
    # Use a herestring (<<<) so the while body runs in the parent shell.
    #
    # Two correctness requirements:
    #   1. Only count issues whose stage HAS a handler (_resolve_stage_prompt returns
    #      non-empty) — unhandled stages are silently skipped by _pick_next and must
    #      not keep _remaining > 0 and prevent the break.
    #   2. Guard against empty _board_snapshot: bash executes `while read` once on
    #      an empty herestring with all vars set to "", which would produce a false
    #      _remaining=1 and suppress the break for one spurious iteration.
    _remaining=0
    _board_snapshot=$(_query_board)
    if [[ -n "$_board_snapshot" ]]; then
      while IFS=' ' read -r _rn _rr _rs; do
        [[ -z "$_rn" ]] && continue
        [[ -z "$(_resolve_stage_prompt "$_rs")" ]] && continue
        _ak="ISSUE_ATTEMPTS__$(printf '%s__%s' "$_rn" "$_rs" | tr -c 'a-zA-Z0-9_' '_')"
        eval "_av=\"\${${_ak}:-0}\""
        [[ "$_av" -lt "$MAX_PER_ISSUE" ]] && _remaining=$((_remaining + 1))
      done <<< "$_board_snapshot"
    fi
    unset _board_snapshot _rn _rr _rs _ak _av
    if [[ "$_remaining" -eq 0 ]]; then
      echo "All actionable issues have reached the retry cap. Stopping."
      break
    fi
    unset _remaining
    continue
  fi
  _new_attempts=$((_attempts + 1))
  eval "${_attempt_key}=$_new_attempts"
  _hit_cap=0
  [[ "$_new_attempts" -ge "$MAX_PER_ISSUE" ]] && _hit_cap=1
  unset _new_attempts _attempts _attempt_key

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  _now=$(date +%s)
  _elapsed=$(( _now - RUN_START_TS ))
  _done_total=$(( DONE + FAILED + SKIPPED ))
  _pct=""
  _eta=""
  if [[ $_TOTAL_ACTIONABLE -gt 0 ]]; then
    _pct=" ($(( (_done_total * 100) / _TOTAL_ACTIONABLE ))%)"
    if [[ $_done_total -gt 0 ]]; then
      _avg=$(( _elapsed / _done_total ))
      _left=$(( _TOTAL_ACTIONABLE - _done_total ))
      _eta_s=$(( _avg * _left ))
      _eta="  ETA: ~$(( _eta_s / 60 ))m"
    fi
  fi
  printf "Progress: %d/%d done%s  •  Elapsed: %dm%ds%s\n" \
    "$_done_total" "$_TOTAL_ACTIONABLE" "$_pct" \
    "$(( _elapsed / 60 ))" "$(( _elapsed % 60 ))" "$_eta"
  unset _now _elapsed _done_total _pct _eta _avg _left _eta_s
  # Pre-flight: verify the issue actually exists in ISSUE_REPO before spending
  # a full session on it. A wrong repo (e.g. data_repo ≠ issue repo when a board
  # aggregates multiple repos) would cause the agent to silently fail every time.
  # Also grab the body here so we can print device context without a second API call.
  _issue_json=$(GH_HOST="$GH_HOST" gh issue view "$ISSUE_NUM" --repo "$ISSUE_REPO" \
    --json state,body,labels 2>/dev/null || echo "")
  _issue_state=$(echo "$_issue_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state',''))" 2>/dev/null || echo "")
  # Skip issues labelled 'human-only' — the agent prompt also checks, but checking
  # here prevents a wasted session + spurious "failed silently" warning + retry loop.
  _is_human_only=$(echo "$_issue_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [l.get('name','') for l in d.get('labels',[])]
print('yes' if 'human-only' in names else 'no')
" 2>/dev/null || echo "no")
  if [[ "$_is_human_only" == "yes" ]]; then
    echo "⊘  Pre-flight SKIP: issue #$ISSUE_NUM has 'human-only' label — skipping without a session."
    # Max out the attempt counter so _pick_next won't return this issue again this run.
    _ho_key="ISSUE_ATTEMPTS__$(printf '%s__%s' "$ISSUE_NUM" "$ISSUE_STAGE" | tr -c 'a-zA-Z0-9_' '_')"
    eval "${_ho_key}=${MAX_PER_ISSUE}"
    unset _ho_key
    SKIPPED=$((SKIPPED + 1))
    CURRENT_ISSUE=""
    unset _issue_json _issue_state _is_human_only
    continue
  fi
  unset _is_human_only
  # Skip issues labelled 'needs-human' when no human has responded yet.
  # The agent prompt also checks, but doing it here avoids a wasted session,
  # the spurious "failed silently" warning, and the retry loop.
  _is_needs_human=$(echo "$_issue_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [l.get('name','') for l in d.get('labels',[])]
print('yes' if 'needs-human' in names else 'no')
" 2>/dev/null || echo "no")
  if [[ "$_is_needs_human" == "yes" ]]; then
    # Check whether a human commented after the last watcher/bot blocker comment.
    _human_responded=$(GH_HOST="$GH_HOST" gh issue view "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --json comments --jq '
        ([.comments[] | select(.body | test("watcher|⚠️"))] | last | .createdAt // "") as $blocked |
        [.comments[] |
          select(.createdAt > $blocked
            and .author.login != "github-actions[bot]"
            and (.body | test("watcher|⚠️") | not))] | length' 2>/dev/null || echo "0")
    if [[ "${_human_responded:-0}" -eq 0 ]]; then
      echo "⊘  Pre-flight SKIP: issue #$ISSUE_NUM has 'needs-human' label with no human response — skipping."
      _nh_key="ISSUE_ATTEMPTS__$(printf '%s__%s' "$ISSUE_NUM" "$ISSUE_STAGE" | tr -c 'a-zA-Z0-9_' '_')"
      eval "${_nh_key}=${MAX_PER_ISSUE}"
      unset _nh_key _human_responded _is_needs_human
      SKIPPED=$((SKIPPED + 1))
      CURRENT_ISSUE=""
      unset _issue_json _issue_state
      continue
    fi
    unset _human_responded
  fi
  unset _is_needs_human
  # Skip parent/epic issues that have sub-issues — they are human-managed trackers.
  # Auto-apply 'human-only' label so we never attempt them again.
  _sub_count=$(GH_HOST="$GH_HOST" gh api \
    "repos/${ISSUE_REPO}/issues/${ISSUE_NUM}/sub_issues" \
    --jq 'length' 2>/dev/null || echo "0")
  if [[ "$_sub_count" -gt 0 ]]; then
    echo "⊘  Pre-flight SKIP: issue #$ISSUE_NUM has ${_sub_count} sub-issue(s) — this is a parent/epic issue."
    echo "   Auto-applying 'human-only' label so the watcher ignores it in future runs."
    GH_HOST="$GH_HOST" gh issue edit "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --add-label "human-only" 2>/dev/null || true
    _ho_key="ISSUE_ATTEMPTS__$(printf '%s__%s' "$ISSUE_NUM" "$ISSUE_STAGE" | tr -c 'a-zA-Z0-9_' '_')"
    eval "${_ho_key}=${MAX_PER_ISSUE}"
    unset _ho_key _sub_count
    SKIPPED=$((SKIPPED + 1))
    CURRENT_ISSUE=""
    continue
  fi
  unset _sub_count

  # All pre-flight checks passed — claim the issue with watcher-lock label so
  # parallel containers on the same board don't start a competing session.
  GH_HOST="$GH_HOST" gh issue edit "$ISSUE_NUM" --repo "$ISSUE_REPO" \
    --add-label "watcher-lock" 2>/dev/null || true

  # Now announce the session.
  echo "Starting session for issue #$ISSUE_NUM  [$ISSUE_STAGE]  ($ISSUE_REPO)  ($(date))"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Extract IPs (192.x.x.x) and serials (standalone integers 2-6 digits) from issue body.
  # Shows the devices this session will likely operate on, as a quick at-a-glance hint.
  _issue_body=$(echo "$_issue_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('body',''))" 2>/dev/null || echo "")
  if [[ -n "$_issue_body" ]]; then
    _devs=$(echo "$_issue_body" | grep -oE '192\.[0-9]+\.[0-9]+\.[0-9]+' | sort -u | tr '\n' ' ' | sed 's/ $//') || true
    _serials=$(echo "$_issue_body" | grep -oE '\b[0-9]{2,6}\b' | sort -u | tr '\n' ' ' | sed 's/ $//') || true
    [[ -n "$_devs" ]] && echo "  Devices (IPs):    $_devs"
    [[ -n "$_serials" && -z "$_devs" ]] && echo "  Devices (serials): $_serials"
  fi
  unset _issue_json _issue_body _devs _serials
  if [[ -z "$_issue_state" ]]; then
    echo "⚠  Pre-flight FAIL: issue #$ISSUE_NUM not found in $ISSUE_REPO — skipping."
    GH_HOST="$GH_HOST" gh issue comment "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --body "<!-- watcher-run: preflight-fail -->⚠️ **watcher-run**: Pre-flight failed — issue #${ISSUE_NUM} could not be fetched from \`${ISSUE_REPO}\`. Check that ISSUE_REPO is correct." \
      2>/dev/null || true
    FAILED=$((FAILED + 1))
    CURRENT_ISSUE=""
    continue
  fi
  unset _issue_state

  HANDLER=$(_resolve_stage_prompt "$ISSUE_STAGE")

  # Render prompt — built-in vars plus any consumer env vars are available.
  # ISSUE_REPO is the actual repo that owns this issue (may differ from REPO when
  # a project board aggregates issues from multiple repos). Prompt templates should
  # use ${ISSUE_REPO} for gh CLI calls and ${REPO} only when they need the
  # data_repo context (e.g. for cost attribution).
  PROMPT=$(ISSUE_NUM="$ISSUE_NUM" REPO="$ISSUE_REPO" ISSUE_REPO="$ISSUE_REPO" \
    GH_HOST="$GH_HOST" ISSUE_STAGE="$ISSUE_STAGE" \
    PROJECT_NUM="$PROJECT_NUM" PROJECT_ID="$PROJECT_ID" PROJECT_OWNER="$ORG" PROJECT_ENTITY="$PROJECT_ENTITY" \
    ALLOWED_REPOS="$ALLOWED_REPOS" \
    envsubst '${ISSUE_NUM} ${REPO} ${ISSUE_REPO} ${GH_HOST} ${ISSUE_STAGE} ${PROJECT_NUM} ${PROJECT_ID} ${PROJECT_OWNER} ${PROJECT_ENTITY} ${ALLOWED_REPOS}' \
    < "$HANDLER")

  # Snapshot session-state dir and store issue context in globals so that
  # _on_interrupt can run _finalize_session even if we are killed mid-session.
  _PRE_SESSION_SNAPSHOT=$(ls ~/.copilot/session-state/ 2>/dev/null | sort || true)
  _PRE_SESSION_ISSUE_REPO="$ISSUE_REPO"
  _PRE_SESSION_ISSUE_STAGE="$ISSUE_STAGE"
  ISSUE_START_TS=$(date +%s)

  ISSUE_EXIT=0
  _SESSION_TIMEOUT_HOURS="${SESSION_TIMEOUT_HOURS:-$(_cfg_optional watcher.session_timeout_hours || echo '')}"
  _SESSION_TIMEOUT_HOURS="${_SESSION_TIMEOUT_HOURS:-4}"
  # Refresh MODEL from MODELS_LIST in case fallback advanced the index.
  [[ ${#MODELS_LIST[@]} -gt 0 ]] && MODEL="${MODELS_LIST[$CURRENT_MODEL_IDX]}"
  _MODEL_FLAG=""
  [[ -n "${MODEL:-}" ]] && _MODEL_FLAG="--model ${MODEL}"
  [[ -n "${MODEL:-}" ]] && echo "  Model: ${MODEL} (priority $((CURRENT_MODEL_IDX + 1))/${#MODELS_LIST[@]})"
  if [[ -n "$LOG_DIR" ]]; then
    ISSUE_LOG="$LOG_DIR/issue-${ISSUE_NUM}-${DATE_TAG}.log"
    ISSUE_MD="$LOG_DIR/issue-${ISSUE_NUM}-${DATE_TAG}-session.md"
    echo "  Per-issue log: $ISSUE_LOG (timeout: ${_SESSION_TIMEOUT_HOURS}h)"
    if (cd "$SESSION_DIR" && GH_HOST="$GH_HOST" \
        timeout "${_SESSION_TIMEOUT_HOURS}h" \
        gh copilot -- \
        ${_MODEL_FLAG:+$_MODEL_FLAG} \
        -p "$PROMPT" \
        --yolo \
        --no-ask-user \
        --share="$ISSUE_MD") \
        2>&1 | tee "$ISSUE_LOG"; then
      :
    else
      ISSUE_EXIT=$?
    fi
  else
    if ! (cd "$SESSION_DIR" && GH_HOST="$GH_HOST" \
        timeout "${_SESSION_TIMEOUT_HOURS}h" \
        gh copilot -- \
        ${_MODEL_FLAG:+$_MODEL_FLAG} \
        -p "$PROMPT" \
        --yolo \
        --no-ask-user); then
      ISSUE_EXIT=$?
    fi
  fi

  # If the session timed out (exit 124), mark needs-human and skip finalisation.
  if [[ $ISSUE_EXIT -eq 124 ]]; then
    echo "  ⏱ SESSION TIMEOUT after ${_SESSION_TIMEOUT_HOURS}h on issue #${ISSUE_NUM} — marking needs-human" >&2
    GH_HOST="$GH_HOST" gh issue comment "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --body "## ⏱ Session timeout

The pipeline agent session exceeded ${_SESSION_TIMEOUT_HOURS}h and was killed.

**Issue:** #${ISSUE_NUM} (stage: ${ISSUE_STAGE})
**Action needed:** investigate logs at \`$LOG_DIR\`, then re-queue or advance manually." \
      2>/dev/null || true
    GH_HOST="$GH_HOST" gh issue edit "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --add-label "needs-human" 2>/dev/null || true
  fi

  # sessionEnd hook fires when the gh copilot process exits above ↑
  # Then we run cost attribution regardless of exit status.
  _finalize_session "$_PRE_SESSION_SNAPSHOT" "$ISSUE_NUM" "$ISSUE_REPO" "$ISSUE_STAGE"

  # Release the watcher-lock so the issue is visible to other containers again.
  GH_HOST="$GH_HOST" gh issue edit "$ISSUE_NUM" --repo "$ISSUE_REPO" \
    --remove-label "watcher-lock" 2>/dev/null || true if the issue is still in the same stage it started
  # in, the agent likely failed silently (zero exit but no real work done). Log a
  # clear warning so the human can diagnose without waiting for the retry cap.
  if [[ $ISSUE_EXIT -eq 0 ]]; then
    _stage_after=$(GH_HOST="$GH_HOST" gh api graphql -f query="
    { ${_matched_entity}(login:\"$ORG\") { projectV2(number:$PROJECT_NUM) { items(first:100) {
      nodes {
        content { ... on Issue { number repository { nameWithOwner } } }
        fieldValues(first:10) { nodes {
          ... on ProjectV2ItemFieldSingleSelectValue {
            name field { ... on ProjectV2SingleSelectField { name } }
          }
        }}
      }
    } } } }" 2>/dev/null \
    | jq -r ".data.${_matched_entity}.projectV2.items.nodes[]
        | select(.content.number==$ISSUE_NUM
              and .content.repository.nameWithOwner==\"$ISSUE_REPO\")
        | .fieldValues.nodes[] | select(.field.name==\"Status\") | .name" \
    2>/dev/null | head -1 || echo "")
    if [[ -n "$_stage_after" && "$_stage_after" == "$ISSUE_STAGE" ]]; then
      echo "⚠  Issue #$ISSUE_NUM is still in stage '$ISSUE_STAGE' after session — agent may have failed silently."
      ISSUE_EXIT=1
    fi
    unset _stage_after
  fi

  # Persist state BEFORE clearing pre-session globals to close the interrupt
  # window — if SIGINT fires here, _on_interrupt can still flush attempt counts
  # but the completion token is already on disk.
  if [[ $ISSUE_EXIT -eq 0 ]]; then
    echo "✓ Issue #$ISSUE_NUM completed  ($(date))"
    DONE=$((DONE + 1))
    _state_write "${ISSUE_REPO}:${ISSUE_NUM}:${ISSUE_STAGE}" 2>/dev/null || true
    MODEL_CONSEC_FAIL=0   # success: reset failure counter for current model
  else
    echo "✗ Issue #$ISSUE_NUM failed  ($(date))"
    FAILED=$((FAILED + 1))
    _state_write "" 2>/dev/null || true
    # Model fallback: after 3 consecutive failures advance to the next cheaper model.
    if [[ ${#MODELS_LIST[@]} -gt 1 ]]; then
      MODEL_CONSEC_FAIL=$((MODEL_CONSEC_FAIL + 1))
      if [[ $MODEL_CONSEC_FAIL -ge 3 && $CURRENT_MODEL_IDX -lt $((${#MODELS_LIST[@]} - 1)) ]]; then
        CURRENT_MODEL_IDX=$((CURRENT_MODEL_IDX + 1))
        MODEL="${MODELS_LIST[$CURRENT_MODEL_IDX]}"
        MODEL_CONSEC_FAIL=0
        echo "⚠  3 consecutive failures — switching to fallback model: ${MODEL} (priority $((CURRENT_MODEL_IDX + 1))/${#MODELS_LIST[@]})"
      fi
    fi
    # Write error excerpt for human review on host.
    _failures_append "$ISSUE_NUM" "$ISSUE_STAGE" "$ISSUE_EXIT"
  fi
  # Append one row to the run summary (human-readable on host).
  _summary_append_row "$ISSUE_NUM" "$ISSUE_STAGE" "$ISSUE_EXIT" "${MODEL:-}" "$ISSUE_START_TS"

  # If this was the last allowed attempt AND the issue didn't advance, post a
  # comment flagging it for human review. Fires after the session so the attempt
  # count is accurate. Guard on ISSUE_EXIT so a successful 3rd attempt (issue
  # advanced to a new stage) does not get a misleading "without advancing" comment.
  if [[ "$_hit_cap" == "1" && $ISSUE_EXIT -ne 0 ]]; then
    _skip_comment="<!-- watcher-run: retry-cap -->⚠️ **watcher-run**: Issue #${ISSUE_NUM} was attempted ${MAX_PER_ISSUE} times in stage \`${ISSUE_STAGE}\` this run without advancing — skipping to prevent a retry storm. Please investigate manually."
    _cap_already=$(GH_HOST="$GH_HOST" gh issue view "$ISSUE_NUM" --repo "$ISSUE_REPO" \
      --json comments \
      --jq '[.comments[].body | select(contains("watcher-run: retry-cap"))] | length' \
      2>/dev/null || echo "0")
    if [[ "$_cap_already" == "0" ]]; then
      GH_HOST="$GH_HOST" gh issue comment "$ISSUE_NUM" --repo "$ISSUE_REPO" \
        --body "$_skip_comment" 2>/dev/null || true
    fi
    unset _skip_comment _cap_already
  fi
  unset _hit_cap

  # Clear pre-session globals — no session is in flight between issues.
  _PRE_SESSION_SNAPSHOT=""
  _PRE_SESSION_ISSUE_REPO=""
  _PRE_SESSION_ISSUE_STAGE=""

  echo ""
  CURRENT_ISSUE=""

  # Safety cap — stop if we've processed too many sessions in one run.
  # Include SKIPPED so a retry storm against a single capped issue can't spin forever.
  [[ $((DONE + FAILED + SKIPPED)) -ge $MAX_ISSUES ]] && {
    echo "Reached max_issues cap ($MAX_ISSUES). Stopping."
    break
  }

  # No sleep between issues — immediately re-query the board for the next one.
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "═══════════════════════════════════════════"
echo "Watcher run complete"
echo "  Completed:           $DONE"
echo "  Failed:              $FAILED"
echo "  Skipped (retry cap / human-only): $SKIPPED  (cap=${MAX_PER_ISSUE}; use --max-per-issue N to adjust)"
echo "  Skipped (resume):    $SKIPPED_RESUME"
_run_total_s=$(( $(date +%s) - RUN_START_TS ))
printf "  Total time:          %dm%ds\n" "$(( _run_total_s / 60 ))" "$(( _run_total_s % 60 ))"
unset _run_total_s
echo "  $(date)"
echo "═══════════════════════════════════════════"
echo ""
echo "State file (use --resume --state-file to continue this run):"
echo "  $STATE_FILE"
echo ""
if [[ -n "$LOG_DIR" ]]; then
  echo "Logs saved to: $LOG_DIR"
  echo "  Run summary:         $LOG_DIR/run-summary.md"
  [[ -f "$LOG_DIR/failures.md" ]] && echo "  Failure excerpts:    $LOG_DIR/failures.md"
  echo "  Run log:             $RUN_LOG"
  echo "  Per-issue logs:      $LOG_DIR/issue-*-${DATE_TAG}.log"
  echo "  Session transcripts: $LOG_DIR/issue-*-${DATE_TAG}-session.md"
  echo ""
fi
REPORT_SCRIPT="$SCRIPT_DIR/../src/cost-report.py"
_config_flag=""
[[ -n "$CONFIG_PATH" ]] && _config_flag="--config $CONFIG_PATH"

echo "Generating cost report..."
GH_HOST="$GH_HOST" python3 "$REPORT_SCRIPT" \
  --repo "$REPO" --project "$PROJECT_NUM" --gh-host "$GH_HOST" $_config_flag

echo ""
echo "Generating HTML dashboard → docs/cost-dashboard.html"
GH_HOST="$GH_HOST" python3 "$REPORT_SCRIPT" \
  --repo "$REPO" --project "$PROJECT_NUM" --gh-host "$GH_HOST" \
  --format html $_config_flag
echo "  Done: $(cd "$SCRIPT_DIR/.." && pwd)/docs/cost-dashboard.html"
unset _config_flag

echo ""
echo "═══════════════════════════════════════════"
echo "Next steps:"
echo ""
echo "  View cost report for this project:"
echo "    GH_HOST=$GH_HOST python3 $SCRIPT_DIR/../src/cost-report.py \\"
echo "      --repo $REPO --project $PROJECT_NUM --gh-host $GH_HOST"
echo ""
echo "  Publish updated dashboard to GitHub Pages:"
echo "    cd $SCRIPT_DIR/.."
echo "    GH_HOST=$GH_HOST ./scripts/publish-ghpages.sh --regen"
echo ""
echo "  View live dashboard:"
PAGES_URL=$(GH_HOST="$GH_HOST" gh api "repos/$REPO/pages" --jq '.html_url' 2>/dev/null) || PAGES_URL=""
if [[ -n "$PAGES_URL" && "$PAGES_URL" != "null" ]]; then
  echo "    $PAGES_URL"
else
  echo "    (enable GitHub Pages: Settings → Pages → Branch: gh-pages)"
fi
echo "═══════════════════════════════════════════"
