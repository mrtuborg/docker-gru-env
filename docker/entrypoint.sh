#!/usr/bin/env bash
set -euo pipefail

# Required env vars
: "${GH_TOKEN:?GH_TOKEN must be set}"
: "${WORKSPACE_REPO:?WORKSPACE_REPO must be set}"
: "${GH_HOST:=github.com}"

echo "[entrypoint] Authenticating gh CLI..."
# gh 2.67+ exits with code 1 if you run `gh auth login` while GH_TOKEN is already
# set in the environment (it uses the env var automatically).  Skip explicit login.
if gh auth status --hostname "$GH_HOST" >/dev/null 2>&1; then
  echo "[entrypoint] GH_TOKEN already active for $GH_HOST"
else
  echo "$GH_TOKEN" | gh auth login --hostname "$GH_HOST" --with-token
fi

echo "[entrypoint] Copilot CLI: $(copilot --version 2>/dev/null || echo 'not found')"

echo "[entrypoint] Cloning workspace: $WORKSPACE_REPO"
gh repo clone "$WORKSPACE_REPO" /workspace -- --depth=1
cd /workspace

# Clone linked repos (space-separated NAME=URL pairs)
if [[ -n "${LINKED_REPOS:-}" ]]; then
  for pair in $LINKED_REPOS; do
    name="${pair%%=*}"
    url="${pair#*=}"
    echo "[entrypoint] Cloning linked repo: $name"
    gh repo clone "$url" "/workspace/$name" -- --depth=1
  done
fi

# Always append the built-in defaults to whatever instructions the workspace provides
# (or use them alone if the workspace has none). This ensures container-specific
# rules (non-interactive mode, automation constraints) are always present alongside
# the project-specific rules.
mkdir -p /workspace/.github
if [[ ! -f /workspace/.github/copilot-instructions.md ]]; then
  cp /tools/gru/docker/defaults/copilot-instructions.md /workspace/.github/copilot-instructions.md
  echo "[entrypoint] No workspace copilot-instructions.md — using built-in defaults"
else
  cat >> /workspace/.github/copilot-instructions.md << 'EOF'

---
<!-- CONTAINER DEFAULTS — lower priority than workspace rules above.
     These rules apply inside the automated container session.
     If any rule here conflicts with a workspace rule above, the WORKSPACE RULE wins. -->

EOF
  cat /tools/gru/docker/defaults/copilot-instructions.md >> /workspace/.github/copilot-instructions.md
  echo "[entrypoint] Appended built-in defaults (lower priority) to workspace copilot-instructions.md"
fi

# Copy project extensions into the Copilot data dir so the CLI finds them.
# COPILOT_DATA_HOME overrides the default ~/.copilot location — use it here too.
if [[ -d /workspace/.github/extensions ]]; then
  _ext_dest="${COPILOT_DATA_HOME:-$HOME/.copilot}/extensions"
  mkdir -p "$_ext_dest"
  cp -r /workspace/.github/extensions/. "$_ext_dest/"
fi

# Install built-in skills from docker-gru-env/skills/ into the Copilot skills dir.
# Skills in the repo are the source of truth; the container has no persistent home dir.
_skills_dest="${COPILOT_DATA_HOME:-$HOME/.copilot}/skills"
if [[ -d /tools/gru/skills ]]; then
  mkdir -p "$_skills_dest"
  cp -r /tools/gru/skills/. "$_skills_dest/"
  echo "[entrypoint] Built-in skills installed: $(ls /tools/gru/skills | tr '\n' ' ')"
fi

# Install workspace skills from /workspace/skills/ — loaded after built-ins so that
# a consumer skill with the same name overrides the built-in version.
if [[ -d /workspace/skills ]]; then
  mkdir -p "$_skills_dest"
  cp -r /workspace/skills/. "$_skills_dest/"
  echo "[entrypoint] Workspace skills installed: $(ls /workspace/skills | tr '\n' ' ')"
fi

mkdir -p /data/copilot /logs

# BOARD_DIR selects which subdirectory of /workspace contains config.yml.
# Matches the DIR argument passed to `gh-watch <DIR> start` on the host.
# Default: hil-stress (backward-compatible).
_board_dir="${BOARD_DIR:-hil-stress}"
_cfg="/workspace/${_board_dir}/config.yml"

# Sanity-check: verify the config and its prompts_dir exist before starting.
if [[ ! -f "$_cfg" ]]; then
  echo "[entrypoint] FATAL: config not found: ${_cfg} (set BOARD_DIR to the correct subdir)"
  exit 1
fi
_prompts_dir=$(python3 -c "
import yaml, os, sys
c = yaml.safe_load(open('$_cfg'))
d = c.get('watcher', {}).get('prompts_dir', '')
print(os.path.normpath(os.path.join(os.path.dirname('$_cfg'), d)))
" 2>/dev/null)
if [[ -z "$_prompts_dir" || ! -d "$_prompts_dir" ]]; then
  echo "[entrypoint] FATAL: watcher.prompts_dir '${_prompts_dir}' does not exist — check ${_board_dir}/config.yml"
  exit 1
fi
echo "[entrypoint] Board dir: ${_board_dir} — config OK, prompts_dir: $_prompts_dir"

echo "[entrypoint] Starting watcher-run.sh..."
OVERNIGHT_ARGS="${OVERNIGHT_ARGS:-}"
exec /tools/gru/scripts/watcher-run.sh \
  --config "${_cfg}" \
  --workspace-dir /workspace \
  --log-dir /logs \
  ${OVERNIGHT_ARGS}

# Note: on success, watcher-run.sh commits + pushes changes from /workspace itself.
