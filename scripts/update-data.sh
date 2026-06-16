#!/usr/bin/env bash
# update-data.sh — Sync local Copilot session JSONL files into data/ and commit.
#
# Run this locally after a session to keep the committed data up to date.
# The GitHub Actions workflow uses data/ to regenerate dashboards in CI.
#
# Usage:
#   ./scripts/update-data.sh [--push]
#
#   --push   Also push the commit to origin/main after committing

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$REPO_ROOT/data"
PUSH=false

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

LIVE_SRC="$HOME/.copilot/cost-log.jsonl"
HIST_SRC="$HOME/.copilot/cost-log-historical.jsonl"
ATTR_DB_SRC="$REPO_ROOT/.gru/attributions.db"
CHANGED=false

for SRC in "$LIVE_SRC" "$HIST_SRC"; do
  FNAME="$(basename "$SRC")"
  DEST="$DATA_DIR/$FNAME"
  if [ -f "$SRC" ]; then
    if ! diff -q "$SRC" "$DEST" &>/dev/null 2>&1; then
      cp "$SRC" "$DEST"
      echo "Updated: data/$FNAME"
      CHANGED=true
    else
      echo "No change: data/$FNAME"
    fi
  else
    echo "Missing (skipped): $SRC"
  fi
done

# Sync attributions.db (single source of truth for attribution)
if [ -f "$ATTR_DB_SRC" ]; then
  ATTR_DB_DEST="$DATA_DIR/attributions.db"
  if ! diff -q "$ATTR_DB_SRC" "$ATTR_DB_DEST" &>/dev/null 2>&1; then
    cp "$ATTR_DB_SRC" "$ATTR_DB_DEST"
    echo "Updated: data/attributions.db"
    CHANGED=true
  else
    echo "No change: data/attributions.db"
  fi
else
  echo "Missing (skipped): .gru/attributions.db"
fi

cd "$REPO_ROOT"

if ! $CHANGED; then
  echo "Nothing to commit."
  exit 0
fi

git add data/
git commit -m "data: sync session cost logs $(date -u +%Y-%m-%dT%H:%M:%SZ)

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

echo "Committed."

if $PUSH; then
  git push
  echo "Pushed."
fi
