#!/usr/bin/env bash
# md-watch.sh — process an Obsidian Kanban markdown board: run a non-interactive
# Copilot session for each OPEN card in the actionable column (default "Todo"),
# optionally marking the card done afterwards.
#
# Usage:
#   md-watch.sh <board.md> [--column NAME] [--dry-run] [--apply] [-- copilot-args...]
#
#   --column NAME   actionable column to read open cards from (default: Todo)
#   --dry-run       list the cards that would be processed, then exit
#   --apply         mark each card done ([x]) in the board after a successful run
#   --              pass any following args straight to `copilot`

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KANBAN="$SCRIPT_DIR/../src/md_kanban.py"

COLUMN="Todo"
DRY_RUN=false
APPLY=false
FILE=""
COPILOT_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --column)  COLUMN="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --apply)   APPLY=true; shift ;;
    --)        shift; COPILOT_ARGS+=("$@"); break ;;
    -*)        COPILOT_ARGS+=("$1"); shift ;;
    *)         if [[ -z "$FILE" ]]; then FILE="$1"; else COPILOT_ARGS+=("$1"); fi; shift ;;
  esac
done

if [[ -z "$FILE" ]]; then
  echo "usage: md-watch.sh <board.md> [--column NAME] [--dry-run] [--apply] [-- copilot-args...]" >&2
  exit 1
fi
if [[ ! -f "$FILE" ]]; then
  echo "ERROR: board file not found: $FILE" >&2
  exit 1
fi

TMP_LIST="$(mktemp)"
trap 'rm -f "$TMP_LIST"' EXIT
if ! python3 "$KANBAN" list --file "$FILE" --column "$COLUMN" > "$TMP_LIST"; then
  echo "ERROR: failed to parse board: $FILE" >&2
  exit 1
fi
# Cards are NUL-separated so multi-line card bodies are read as single elements.
mapfile -d '' -t CARDS < "$TMP_LIST"

if [[ ${#CARDS[@]} -eq 0 ]]; then
  echo "[md-watch] no open cards in column '$COLUMN' of $FILE"
  exit 0
fi

echo "[md-watch] ${#CARDS[@]} open card(s) in '$COLUMN' of $FILE:"
for c in "${CARDS[@]}"; do echo "  - ${c%%$'\n'*}"; done

if $DRY_RUN; then
  echo "[md-watch] dry-run — not starting sessions."
  exit 0
fi

rc_all=0
for card in "${CARDS[@]}"; do
  title="${card%%$'\n'*}"
  echo "[md-watch] ===== card: $title ====="
  if copilot -p "$card" "${COPILOT_ARGS[@]}"; then
    if $APPLY; then
      # --card=VALUE form so a title starting with '-' isn't taken as a flag.
      if python3 "$KANBAN" done --file "$FILE" --column "$COLUMN" --card="$title" --write; then
        echo "[md-watch] marked done: $title"
      fi
    fi
  else
    echo "[md-watch] session failed for card: $title" >&2
    rc_all=1
  fi
done

exit "$rc_all"
