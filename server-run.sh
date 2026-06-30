#!/usr/bin/env bash
# Start the gru-server container.
#
# Usage:
#   ./server-run.sh                        # start or restart existing container
#   ./server-run.sh --fresh                # wipe volume + restart
#   ./server-run.sh --rebuild              # build image first, then --fresh
#   ./server-run.sh --seed <config.yml>    # seed DB from config file (implies --fresh)
#   ./server-run.sh --seed                 # seed from baked-in /app/hil-stress-config.yml
#
# GRU_GHE_TOKEN env var — GHE PAT to store in vault during seed (optional)
# GRU_PORT      env var — base port to try (default 9400)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
IMAGE="gru-server:latest"
CONTAINER="gru-server-dev"
PORT="${GRU_PORT:-9400}"
VOLUME="gru-data"
AZURE_DIR="$HOME/.azure"

FRESH=0
REBUILD=0
SEED_CONFIG=""   # empty = not seeding; "-" = use default in image; else = host path

# ── Parse args ────────────────────────────────────────────────────────────────
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  case "${args[$i]}" in
    --fresh)
      FRESH=1 ;;
    --rebuild)
      REBUILD=1; FRESH=1 ;;
    --seed)
      FRESH=1
      next=$((i+1))
      if [[ $next -lt ${#args[@]} && "${args[$next]}" != --* ]]; then
        SEED_CONFIG="${args[$next]}"
        i=$((i+1))
      else
        SEED_CONFIG="-"   # use baked-in default
      fi
      ;;
  esac
  i=$((i+1))
done

# ── Rebuild image ─────────────────────────────────────────────────────────────
if [[ $REBUILD -eq 1 ]]; then
  echo "▶ Rebuilding image …"
  docker build -f "$REPO_ROOT/Dockerfile.server" -t "$IMAGE" "$REPO_ROOT"
  echo "✓ Image built"
fi

# ── Wipe existing container + volume ─────────────────────────────────────────
if [[ $FRESH -eq 1 ]]; then
  echo "▶ Removing existing container and data volume …"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker volume rm "$VOLUME"  2>/dev/null || true
  echo "✓ Cleaned up"
fi

# ── Find a free port ──────────────────────────────────────────────────────────
find_free_port() {
  local p="$1"
  while lsof -iTCP:"$p" -sTCP:LISTEN -t &>/dev/null; do
    echo "  port $p is in use, trying $((p+1)) …" >&2
    (( p++ ))
  done
  echo "$p"
}
PORT=$(find_free_port "$PORT")

# ── Start container ───────────────────────────────────────────────────────────
if docker inspect "$CONTAINER" &>/dev/null; then
  echo "▶ Starting existing container $CONTAINER …"
  docker start "$CONTAINER"
else
  echo "▶ Creating and starting $CONTAINER on port $PORT …"

  AZURE_MOUNT=()
  [[ -d "$AZURE_DIR" ]] && AZURE_MOUNT=(-v "$AZURE_DIR:/root/.azure")

  CONFIG_MOUNT=()
  SEED_ENVS=()
  if [[ -n "$SEED_CONFIG" ]]; then
    SEED_ENVS+=(-e "GRU_SEED=1")
    if [[ -n "${GRU_GHE_TOKEN:-}" ]]; then
      SEED_ENVS+=(-e "GRU_GHE_TOKEN=${GRU_GHE_TOKEN}")
    fi
    if [[ "$SEED_CONFIG" != "-" ]]; then
      HOST_CFG="$(cd "$(dirname "$SEED_CONFIG")" && pwd)/$(basename "$SEED_CONFIG")"
      CONFIG_MOUNT=(-v "$HOST_CFG:/app/seed-config.yml:ro")
      SEED_ENVS+=(-e "GRU_SEED_CONFIG=/app/seed-config.yml")
    fi
  fi

  docker run -d \
    --name "$CONTAINER" \
    -p "${PORT}:9400" \
    -v "${VOLUME}:/data" \
    "${AZURE_MOUNT[@]+"${AZURE_MOUNT[@]}"}" \
    "${CONFIG_MOUNT[@]+"${CONFIG_MOUNT[@]}"}" \
    "${SEED_ENVS[@]+"${SEED_ENVS[@]}"}" \
    "$IMAGE"
fi

echo "✓ gru-server running at http://localhost:${PORT}"
if [[ -n "$SEED_CONFIG" ]]; then
  cfg_label="$( [[ "$SEED_CONFIG" == "-" ]] && echo "built-in hil-stress-config.yml" || echo "$SEED_CONFIG" )"
  echo "  Seeded from: $cfg_label"
  echo "  If no token was provided, authorize via Connectors page after startup."
fi
