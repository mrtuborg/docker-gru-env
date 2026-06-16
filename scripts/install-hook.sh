#!/usr/bin/env bash
# install-hook.sh — Write ~/.copilot/hooks/hooks.json pointing to this repo's cost-sync.py.
#
# Re-run this script any time the repo is moved to a new path.
#
# Usage:
#   ./scripts/install-hook.sh           # install (prompts if file exists)
#   ./scripts/install-hook.sh --force   # overwrite without prompting
#   ./scripts/install-hook.sh --dry-run # print what would be written

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYNC_SCRIPT="$REPO_ROOT/src/cost-sync.py"
HOOKS_DIR="$HOME/.copilot/hooks"
HOOKS_FILE="$HOOKS_DIR/hooks.json"
FORCE=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)   FORCE=true;   shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

CONTENT=$(cat <<EOF
{
  "sessionEnd": {
    "run": "python3 $SYNC_SCRIPT",
    "description": "Append one cost record for the just-closed session to ~/.copilot/cost-log.jsonl. COPILOT_SESSION_ID is set by the CLI before invoking this hook.",
    "fallback": "python3 $SYNC_SCRIPT --session-id <SESSION_ID>"
  }
}
EOF
)

if $DRY_RUN; then
  echo "Would write to: $HOOKS_FILE"
  echo "$CONTENT"
  exit 0
fi

if [[ -f "$HOOKS_FILE" ]] && ! $FORCE; then
  echo "Existing $HOOKS_FILE:"
  cat "$HOOKS_FILE"
  echo ""
  read -rp "Overwrite? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

mkdir -p "$HOOKS_DIR"
echo "$CONTENT" > "$HOOKS_FILE"
echo "✓ Wrote $HOOKS_FILE"
echo "  hook → python3 $SYNC_SCRIPT"
