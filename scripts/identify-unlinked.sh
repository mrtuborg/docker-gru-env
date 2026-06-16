#!/usr/bin/env bash
# identify-unlinked.sh — Find and triage unlinked Copilot sessions.
#
# Workflow:
#   1. Run cost-link.py  — auto-attribute sessions via commits/PRs/branches
#   2. Run cost-identify-unlinked.py — append remaining unlinked to manual-attributions.yml
#   3. Print next steps
#
# Usage:
#   ./scripts/identify-unlinked.sh [--no-auto] [--dry-run] [--apply] [--min-cost N]
#
#   --no-auto      Skip the automated cost-link.py step
#   --dry-run      Show what would be appended without writing
#   --apply        After identifying, immediately open manual-attributions.yml
#                  and prompt to apply when ready
#   --min-cost N   Only surface sessions costing more than N USD (default: 0.01)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
cd "$REPO_DIR"

# ── Config ──────────────────────────────────────────────────────────────────
CONFIG_FILE=".gru/config.yml"
MANUAL_FILE=".gru/manual-attributions.yml"

GH_HOST="${GH_HOST:-}"
if [[ -z "$GH_HOST" && -f "$CONFIG_FILE" ]]; then
    GH_HOST="$(grep -E '^gh_host:' "$CONFIG_FILE" | awk '{print $2}' | tr -d '"' || true)"
fi

# ── Flags ────────────────────────────────────────────────────────────────────
AUTO=true
DRY_RUN=false
APPLY=false
MIN_COST=0.01

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-auto)   AUTO=false ;;
        --dry-run)   DRY_RUN=true ;;
        --apply)     APPLY=true ;;
        --min-cost)  MIN_COST="$2"; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Identify Unlinked Copilot Sessions         ║"
echo "╚══════════════════════════════════════════════╝"

# ── Step 1: Auto-attribution ─────────────────────────────────────────────────
if $AUTO; then
    echo ""
    echo "▶ Step 1 — Auto-attribution via commits/PRs/branches…"
    if [[ -z "$GH_HOST" ]]; then
        echo "  ⚠  GH_HOST not set — skipping auto-attribution"
        echo "     Set GH_HOST=your.ghe.com or run with --no-auto"
    else
        export GH_HOST
        if python3 src/cost-link.py --apply 2>&1 | grep -v "^$"; then
            echo "  Done."
        fi
    fi
else
    echo ""
    echo "▶ Step 1 — Skipping auto-attribution (--no-auto)"
fi

# ── Step 2: Find remaining unlinked ──────────────────────────────────────────
echo ""
echo "▶ Step 2 — Scanning for new unlinked sessions…"

IDENTIFY_ARGS=(--min-cost "$MIN_COST" --config "$CONFIG_FILE" --file "$MANUAL_FILE")
$DRY_RUN && IDENTIFY_ARGS+=(--dry-run)

python3 src/cost-identify-unlinked.py "${IDENTIFY_ARGS[@]}"
IDENTIFY_EXIT=$?

if [[ $IDENTIFY_EXIT -ne 0 ]]; then
    echo "ERROR: cost-identify-unlinked.py failed" >&2
    exit $IDENTIFY_EXIT
fi

# ── Step 3: Apply (optional) ─────────────────────────────────────────────────
if $APPLY && ! $DRY_RUN; then
    echo ""
    echo "▶ Step 3 — Apply when ready…"
    echo "  Opening $MANUAL_FILE …"
    "${EDITOR:-vi}" "$MANUAL_FILE"
    echo ""
    echo "  Previewing patches…"
    python3 src/cost-link-manual.py --file "$MANUAL_FILE"
    echo ""
    read -r -p "  Apply patches? [y/N] " CONFIRM
    if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
        python3 src/cost-link-manual.py --file "$MANUAL_FILE" --apply
        echo ""
        echo "  Rebuilding dashboard…"
        GH_HOST="$GH_HOST" ./scripts/build-dashboard.sh
    else
        echo "  Skipped. Run manually:"
        echo "    python3 src/cost-link-manual.py --apply"
        echo "    ./scripts/build-dashboard.sh"
    fi
fi
