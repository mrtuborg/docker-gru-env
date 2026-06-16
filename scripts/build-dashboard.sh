#!/usr/bin/env bash
# build-dashboard.sh — Regenerate cost dashboards and publish to GitHub Pages.
#
# Usage:
#   ./scripts/build-dashboard.sh [--config PATH] [--regen-only] [--publish-only] [--dry-run]
#
# Options:
#   --config PATH    Workflow config YAML (default: auto-detect .gru/config.yml)
#   --regen-only     Regenerate HTML dashboards into docs/ but do not publish
#   --publish-only   Push existing docs/ to Pages without regenerating
#   --dry-run        Build locally, skip git push

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG=""
REGEN_ONLY=false
PUBLISH_ONLY=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)       CONFIG="$2"; shift 2 ;;
    --regen-only)   REGEN_ONLY=true; shift ;;
    --publish-only) PUBLISH_ONLY=true; shift ;;
    --dry-run)      DRY_RUN=true; shift ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Auto-detect config
if [[ -z "$CONFIG" ]]; then
  for candidate in \
    "$REPO_ROOT/.gru/config.yml" \
    "$REPO_ROOT/.gru/config.yaml"; do
    if [[ -f "$candidate" ]]; then
      CONFIG="$candidate"
      break
    fi
  done
fi

if [[ -z "$CONFIG" ]]; then
  echo "ERROR: No config found. Pass --config PATH or create .gru/config.yml" >&2
  exit 1
fi

_cfg() {
  python3 "$REPO_ROOT/src/workflow_config.py" --config "$CONFIG" --get "$1" 2>/dev/null || true
}

GH_HOST=$(_cfg gh_host)
GH_REPO=$(_cfg data_repo)
PAGES_REPO=$(_cfg pages_repo)

# report_repo: the repo whose owner's projects are scanned for --all-projects.
# Defaults to project.owner/data_repo_name when project.owner differs from
# data_repo owner, so projects in the org are discovered rather than the
# personal account.
REPORT_REPO=$(_cfg report_repo)
if [[ -z "$REPORT_REPO" ]]; then
  PROJECT_OWNER=$(_cfg project.owner)
  DATA_REPO_OWNER="${GH_REPO%%/*}"
  DATA_REPO_NAME="${GH_REPO##*/}"
  if [[ -n "$PROJECT_OWNER" && "$PROJECT_OWNER" != "$DATA_REPO_OWNER" ]]; then
    REPORT_REPO="${PROJECT_OWNER}/${DATA_REPO_NAME}"
  else
    REPORT_REPO="$GH_REPO"
  fi
fi

echo "=== build-dashboard ==="
echo "Config:     $CONFIG"
echo "GH host:    $GH_HOST"
echo "Data repo:  $GH_REPO"
echo "Report org: $REPORT_REPO"
echo "Pages repo: $PAGES_REPO"
echo ""

# ── Regenerate ────────────────────────────────────────────────────────────────
if ! $PUBLISH_ONLY; then
  echo "▶ Regenerating dashboards..."
  cd "$REPO_ROOT"
  GH_HOST="$GH_HOST" python3 src/cost-report.py \
    --repo "$REPORT_REPO" \
    --gh-host "$GH_HOST" \
    --format html \
    --all-projects
  echo "  Done → $(pwd)/docs/"
  echo ""
fi

# ── Sync costs to project boards ──────────────────────────────────────────────
if ! $PUBLISH_ONLY; then
  PROJECT_OWNER=$(_cfg project.owner)
  if [[ -z "$PROJECT_OWNER" ]]; then
    PROJECT_OWNER="${REPORT_REPO%%/*}"
  fi
  echo "▶ Syncing costs to project boards..."
  BOARD_SYNC_ARGS=(--project-owner "$PROJECT_OWNER" --all-projects --gh-host "$GH_HOST")
  $DRY_RUN && BOARD_SYNC_ARGS+=(--dry-run)
  GH_HOST="$GH_HOST" python3 "$REPO_ROOT/src/cost-board-sync.py" \
    "${BOARD_SYNC_ARGS[@]}" || echo "  WARNING: cost-board-sync failed (non-fatal)"
  echo ""
fi

# ── Publish ───────────────────────────────────────────────────────────────────
if ! $REGEN_ONLY; then
  PUBLISH_ARGS=(--config "$CONFIG")
  $DRY_RUN && PUBLISH_ARGS+=(--dry-run)

  "$SCRIPT_DIR/publish-ghpages.sh" "${PUBLISH_ARGS[@]}"
fi
