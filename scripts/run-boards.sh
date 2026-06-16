#!/usr/bin/env bash
# run-boards.sh — Daemon wrapper for watcher-run.sh across multiple project boards.
#
# Discovers all .gru/config*.yml files in the workspace, maps them to
# board numbers, and runs watcher-run.sh for each board in a continuous loop.
# Multiple configs per repo are supported (e.g. config.yml for devs, config-p13.yml for support).
# Designed to run as a systemd service or a background daemon.
#
# Usage:
#   ./scripts/run-boards.sh [OPTIONS]
#
# Options:
#   --board N            Only run this board number (repeat for multiple, default: all)
#   --workspace DIR      Root dir to scan for configs (default: $COPILOT_WORKSPACE or ~/ws/platform)
#   --interval SECONDS   Sleep between full cycles (default: 1800 = 30 min; only with --loop)
#   --log-dir DIR        Write per-board logs here (default: /tmp/copilot-boards)
#   --loop               Run continuously as a daemon (default: run one cycle then exit)
#   --dry-run            Pass --dry-run to each watcher-run.sh invocation
#   -h, --help
#
# Systemd unit example:
#   [Unit]
#   Description=Copilot watcher-run boards daemon
#   After=network.target
#
#   [Service]
#   User=ci
#   EnvironmentFile=/etc/copilot-boards.env   # GH_HOST, HOME, etc.
#   ExecStart=/home/ci/copilot-workflow/scripts/run-boards.sh \
#       --workspace /home/ci/ws/platform \
#       --log-dir /var/log/copilot-boards \
#       --interval 1800 \
#       --loop
#   Restart=always
#   RestartSec=60
#
#   [Install]
#   WantedBy=multi-user.target

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERNIGHT="${SCRIPT_DIR}/watcher-run.sh"

export GH_PAGER=cat

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BOARDS=()           # empty = show help; use --all to run all boards
RUN_ALL=false
WORKSPACE="${COPILOT_WORKSPACE:-${HOME}/ws/platform}"
INTERVAL=1800
LOG_DIR="/tmp/copilot-boards"
ONCE=true
LOOP=false
DRY_RUN=false

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --board)      BOARDS+=("$2"); shift 2 ;;
    --all)        RUN_ALL=true; shift ;;
    --workspace)  WORKSPACE="$2"; shift 2 ;;
    --interval)   INTERVAL="$2"; shift 2 ;;
    --log-dir)    LOG_DIR="$2"; shift 2 ;;
    --once)       ONCE=true; LOOP=false; shift ;;
    --loop)       LOOP=true; ONCE=false; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)    BOARDS=(); RUN_ALL=false; break ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Discover configs: returns "<board_number> <config_path>" sorted by number
# ---------------------------------------------------------------------------
_discover_configs() {
  find "$WORKSPACE" -maxdepth 4 -name "config*.yml" -path "*/.gru/*" 2>/dev/null \
  | while read -r cfg; do
      num=$(python3 "$SCRIPT_DIR/../src/workflow_config.py" \
              --config "$cfg" --get project.number 2>/dev/null || true)
      name=$(python3 "$SCRIPT_DIR/../src/workflow_config.py" \
              --config "$cfg" --get project.name 2>/dev/null || true)
      if [[ -z "$name" ]]; then
        name=$(python3 "$SCRIPT_DIR/../src/workflow_config.py" \
                --config "$cfg" --get data_repo 2>/dev/null || true)
      fi
      if [[ -n "$num" ]]; then
        printf "%s\t%s\t%s\n" "$num" "$name" "$cfg"
      fi
    done \
  | sort -n
}
if [[ ${#BOARDS[@]} -eq 0 && "$RUN_ALL" == "false" ]]; then
  echo "Usage: $(basename "$0") --board N [--board M ...] [OPTIONS]"
  echo "       $(basename "$0") --all [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --board N        Run this board number (repeat for multiple)"
  echo "  --all            Run all discovered boards"
  echo "  --workspace DIR  Root dir to scan for configs (default: \$COPILOT_WORKSPACE or ~/ws/platform)"
  echo "  --interval SEC   Sleep between full cycles (default: 1800 = 30 min); only used with --loop"
  echo "  --log-dir DIR    Write per-board logs here (default: /tmp/copilot-boards)"
  echo "  --loop           Run continuously (daemon mode); default is to exit after one cycle"
  echo "  --dry-run        Pass --dry-run to each watcher-run.sh"
  echo ""
  echo "Discovered boards in $WORKSPACE:"
  echo ""
  _discover_configs | while IFS=$'\t' read -r num name cfg; do
    printf "  --board %-4s  %-40s  %s\n" "$num" "$name" "$cfg"
  done
  echo ""
  exit 0
fi

# ---------------------------------------------------------------------------
# Filter to requested boards (--board N) or all (--all)
# ---------------------------------------------------------------------------
_filter_boards() {
  local all="$1"
  if $RUN_ALL || [[ ${#BOARDS[@]} -eq 0 ]]; then
    echo "$all"
    return
  fi
  while IFS=$'\t' read -r num name cfg; do
    for b in "${BOARDS[@]}"; do
      if [[ "$num" == "$b" ]]; then
        printf "%s\t%s\t%s\n" "$num" "$name" "$cfg"
        break
      fi
    done
  done <<< "$all"
}

# ---------------------------------------------------------------------------
# Run one pass across all selected boards
# ---------------------------------------------------------------------------
_run_cycle() {
  local date_tag; date_tag=$(date +%Y%m%d-%H%M%S)
  local all_configs; all_configs=$(_discover_configs)
  local selected; selected=$(_filter_boards "$all_configs")

  if [[ -z "$selected" ]]; then
    if [[ ${#BOARDS[@]} -gt 0 ]]; then
      echo "[$(date '+%H:%M:%S')] No configs found for board(s): ${BOARDS[*]}"
    else
      echo "[$(date '+%H:%M:%S')] No .gru/config.yml files found in $WORKSPACE"
    fi
    return
  fi

  echo ""
  echo "╔══════════════════════════════════════════════════════════════════╗"
  echo "║  Copilot boards cycle — $(date)  ║"
  echo "╚══════════════════════════════════════════════════════════════════╝"
  echo ""

  while IFS=$'\t' read -r board_num name config_path; do
    echo "┌─────────────────────────────────────────────────────────────────"
    echo "│  Board #${board_num}  ${name}  config: ${config_path}"
    echo "└─────────────────────────────────────────────────────────────────"

    local board_log="${LOG_DIR}/board-${board_num}-${date_tag}.log"
    mkdir -p "$LOG_DIR"

    local args=(--config "$config_path")
    $DRY_RUN && args+=(--dry-run)

    if "$OVERNIGHT" "${args[@]}" 2>&1 | tee "$board_log"; then
      echo "  ✓ Board #${board_num} cycle complete"
    else
      echo "  ✗ Board #${board_num} watcher-run exited with error (see $board_log)"
    fi

    echo ""
  done <<< "$selected"

  # Prune logs older than 14 days
  find "$LOG_DIR" -name "board-*-*.log" -mtime +14 -delete 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "=== run-boards daemon ==="
echo "Workspace:  $WORKSPACE"
echo "Interval:   ${INTERVAL}s"
echo "Log dir:    $LOG_DIR"
$RUN_ALL && echo "Boards:     all" || echo "Boards:     ${BOARDS[*]}"
$DRY_RUN && echo "Mode:       DRY RUN"
$LOOP    && echo "Mode:       LOOP (daemon)" || echo "Mode:       ONE SHOT (use --loop for daemon)"
echo ""

echo "Discovered boards:"
_discover_configs | while IFS=$'\t' read -r num name cfg; do
  marker="○ (skipped)"
  if $RUN_ALL; then
    marker="●"
  else
    for b in "${BOARDS[@]}"; do
      if [[ "$num" == "$b" ]]; then marker="●"; break; fi
    done
  fi
  printf "  %s  #%-4s  %-40s  %s\n" "$marker" "$num" "$name" "$cfg"
done
echo ""

if ! $LOOP; then
  _run_cycle
  exit 0
fi

# Continuous loop (--loop / daemon mode)
while true; do
  _run_cycle

  echo "Sleeping ${INTERVAL}s until next cycle… ($(date))"
  sleep "$INTERVAL"
done
