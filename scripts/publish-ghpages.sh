#!/usr/bin/env bash
# publish-ghpages.sh — Regenerate dashboards and publish docs/ to a Pages branch.
#
# Usage:
#   ./scripts/publish-ghpages.sh --config PATH [--regen] [--dry-run]
#
#   --config PATH  Path to workflow config YAML (required); supplies gh_host,
#                  data_repo, pages_repo, and optional pages.branch
#   --regen        Regenerate all HTML dashboards before publishing (requires
#                  gh_host to be reachable and valid credentials)
#   --dry-run      Build the pages tree locally but do not push
#
# Cross-repo push: if pages_repo in config differs from the origin remote,
# a temporary "pages-target" remote is used for the push and then removed.
# Auth: GH_TOKEN or GHE_TOKEN env vars are used when set; otherwise the gh
# credential helper is relied upon.
#
# Config keys used:
#   pages_repo    (required) — target repo for HTML output
#   pages.branch  (optional, default: gh-pages) — branch to push to in pages_repo

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGES_BRANCH="gh-pages"
REGEN=false
DRY_RUN=false
CONFIG=""

# Initialise before trap so set -u doesn't fire inside the handler.
TMPDIR_WORKTREE=""
PUSH_REMOTE="origin"
TEMP_REMOTE_ADDED=false

_cleanup() {
  if [ -n "$TMPDIR_WORKTREE" ]; then
    git -C "$REPO_ROOT" worktree remove --force "$TMPDIR_WORKTREE" 2>/dev/null || true
    rm -rf "$TMPDIR_WORKTREE"
  fi
  if $TEMP_REMOTE_ADDED; then
    git -C "$REPO_ROOT" remote remove pages-target 2>/dev/null || true
  fi
  # Remove temp refs used during publish
  git -C "$REPO_ROOT" update-ref -d refs/pages-publish/HEAD 2>/dev/null || true
  git -C "$REPO_ROOT" branch -D _pages-publish-tmp 2>/dev/null || true
}
trap _cleanup EXIT

# ── Parse args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      [[ $# -ge 2 ]] || { echo "ERROR: --config requires a PATH argument" >&2; exit 1; }
      CONFIG="$2"; shift 2 ;;
    --regen)   REGEN=true;   shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$CONFIG" ]; then
  echo "ERROR: --config PATH is required" >&2
  echo "Usage: $0 --config PATH [--regen] [--dry-run]" >&2
  exit 1
fi

# ── Load config ───────────────────────────────────────────────────────────────
cd "$REPO_ROOT"

_cfg() { python3 src/workflow_config.py --config "$CONFIG" --get "$1"; }

GH_HOST=$(_cfg gh_host)
GH_REPO=$(_cfg data_repo)
PAGES_REPO=$(_cfg pages_repo)
PAGES_BRANCH=$(python3 src/workflow_config.py --config "$CONFIG" --get pages.branch 2>/dev/null || echo "gh-pages")
PROJECT_OWNER=$(python3 src/workflow_config.py --config "$CONFIG" --get project.owner 2>/dev/null || true)

# ── Detect if pages_repo differs from origin; set up remote if needed ─────────
# Normalize a git remote URL to "host/owner/repo" for comparison.
_normalize_url() {
  local url="$1"
  url="${url#https://}"
  url="${url#http://}"
  url="${url#git://}"
  if [[ "$url" == *"@"*":"* ]]; then
    url="${url#*@}"       # strip user@
    url="${url/://}"      # replace first : with /
  fi
  url="${url%.git}"
  echo "$url"
}

ORIGIN_URL=$(git remote get-url origin)
ORIGIN_SLUG=$(_normalize_url "$ORIGIN_URL")
PAGES_SLUG="${GH_HOST}/${PAGES_REPO%.git}"

if [ "$ORIGIN_SLUG" != "$PAGES_SLUG" ]; then
  PUSH_REMOTE="pages-target"
  TEMP_REMOTE_ADDED=true
  TOKEN="${GH_TOKEN:-${GHE_TOKEN:-}}"
  if [ -n "$TOKEN" ]; then
    PAGES_URL="https://x-access-token:${TOKEN}@${GH_HOST}/${PAGES_REPO%.git}.git"
  else
    # No token — mirror the SSH format from origin (e.g. git@host:owner/repo.git)
    ORIGIN_SSH_USER=$(echo "$ORIGIN_URL" | grep -oE '^[^@]+@' || echo "git@")
    PAGES_URL="${ORIGIN_SSH_USER}${GH_HOST}:${PAGES_REPO%.git}.git"
  fi
  git remote add pages-target "$PAGES_URL"
fi

# ── 1. Optionally regenerate dashboards ──────────────────────────────────────
if $REGEN; then
  echo "▶ Regenerating dashboards…"
  _proj_owner_flag=""
  [[ -n "$PROJECT_OWNER" ]] && _proj_owner_flag="--project-owner $PROJECT_OWNER"
  GH_HOST="$GH_HOST" python3 src/cost-report.py \
    --repo "$GH_REPO" \
    --gh-host "$GH_HOST" \
    --format html \
    --all-projects \
    $_proj_owner_flag
  unset _proj_owner_flag
  echo "  Done."
fi

# ── 2. Ensure docs/ exists ────────────────────────────────────────────────────
if [ ! -f docs/index.html ]; then
  echo "ERROR: docs/index.html not found. Run with --regen first." >&2
  exit 1
fi

# ── 3. Prepare a temporary worktree for the gh-pages branch ──────────────────
TMPDIR_WORKTREE="$(mktemp -d)"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

echo "▶ Preparing ${PAGES_BRANCH} branch…"

# For cross-repo pushes the fetched content lands in
# refs/remotes/<remote>/<branch>, not in the local branch of the same name.
# Always use a detached worktree so we never collide with the current branch
# and always push HEAD:<branch> rather than a named local ref.
_worktree_from_ref() {
  local ref="$1" dir="$2"
  git worktree add --detach "$dir" "$ref"
}

if $DRY_RUN; then
  # In dry-run mode, skip all remote operations to guarantee exit 0.
  LOCAL_EXISTS=$(git branch --list "$PAGES_BRANCH")
  if [ -z "$LOCAL_EXISTS" ]; then
    echo "  Creating orphan branch $PAGES_BRANCH (dry-run, local only)…"
    git worktree add --orphan -B "$PAGES_BRANCH" "$TMPDIR_WORKTREE"
  else
    _worktree_from_ref "refs/heads/$PAGES_BRANCH" "$TMPDIR_WORKTREE"
  fi
else
  REMOTE_EXISTS=$(git ls-remote --exit-code "$PUSH_REMOTE" "refs/heads/$PAGES_BRANCH" 2>/dev/null && echo yes || echo no)

  if [ "$REMOTE_EXISTS" = "yes" ]; then
    # Fetch the pages branch into a temporary remote-tracking ref so we
    # never touch the local branch (which may be the currently checked-out one).
    git fetch "$PUSH_REMOTE" "${PAGES_BRANCH}:refs/pages-publish/HEAD" --quiet
    _worktree_from_ref "refs/pages-publish/HEAD" "$TMPDIR_WORKTREE"
  else
    echo "  Creating orphan branch ${PAGES_BRANCH}..."
    # Use a throwaway local name to avoid colliding with the current branch.
    git worktree add --orphan -B "_pages-publish-tmp" "$TMPDIR_WORKTREE"
  fi
fi

# ── 4. Sync docs/ into the worktree ──────────────────────────────────────────
echo "▶ Syncing docs/ → ${PAGES_BRANCH}…"
find "$TMPDIR_WORKTREE" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
cp -r docs/. "$TMPDIR_WORKTREE/"

# ── 5. Commit ─────────────────────────────────────────────────────────────────
cd "$TMPDIR_WORKTREE"
git add --all

if git diff --cached --quiet; then
  echo "  No changes to publish."
  exit 0
fi

COMMIT_MSG="chore: publish dashboards $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git commit -m "$COMMIT_MSG" \
  --author="$(git -C "$REPO_ROOT" log -1 --format='%an <%ae>')"

echo "▶ Committed: $COMMIT_MSG"

# ── 6. Push ───────────────────────────────────────────────────────────────────
if $DRY_RUN; then
  echo "  DRY-RUN: skipping push."
else
  # Force-push: the pages branch contains only generated HTML that is always
  # rebuilt from scratch, so diverged history is expected and correct.
  git push --force "$PUSH_REMOTE" "HEAD:refs/heads/${PAGES_BRANCH}"
  # Clean up the temporary fetch ref (best-effort)
  git -C "$REPO_ROOT" update-ref -d refs/pages-publish/HEAD 2>/dev/null || true
  echo ""
  echo "✅ Published!"
  # GHE Pages URL: https://<owner>-<repo-name-dots-replaced-by-dashes>.pages.<host>
  _pages_owner="${PAGES_REPO%%/*}"
  _pages_name="${PAGES_REPO##*/}"
  _pages_name_norm="${_pages_name//./-}"
  echo "   URL: https://${_pages_owner}-${_pages_name_norm}.pages.${GH_HOST}"
  echo ""
  echo "   If this is the first publish, enable Pages in the target repo settings:"
  echo "   Settings → Pages → Source: Deploy from branch → ${PAGES_BRANCH} / (root)"
fi
