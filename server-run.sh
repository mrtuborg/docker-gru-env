#!/usr/bin/env bash
# Start the gru-server container.
#
# Usage:
#   ./server-run.sh                        # start or restart existing container
#   ./server-run.sh --fresh                # wipe volume + restart
#   ./server-run.sh --rebuild              # build image first, then --fresh
#   ./server-run.sh --seed <config.yml>    # seed DB from config on start (idempotent upsert)
#   ./server-run.sh --fresh --seed <cfg>   # wipe + seed fresh
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
SEED_CONFIG=""

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
      next=$((i+1))
      if [[ $next -lt ${#args[@]} && "${args[$next]}" != --* ]]; then
        SEED_CONFIG="${args[$next]}"
        i=$((i+1))
      else
        echo "Error: --seed requires a config file path" >&2
        echo "Usage: ./server-run.sh --seed <config.yml>" >&2
        exit 1
      fi
      ;;
  esac
  i=$((i+1))
done

# Validate seed config file exists
if [[ -n "$SEED_CONFIG" && ! -f "$SEED_CONFIG" ]]; then
  echo "Error: seed config file not found: $SEED_CONFIG" >&2
  exit 1
fi

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

  # Run seed against already-running container if requested
  if [[ -n "$SEED_CONFIG" ]]; then
    HOST_CFG="$(cd "$(dirname "$SEED_CONFIG")" && pwd)/$(basename "$SEED_CONFIG")"
    echo "▶ Seeding from $HOST_CFG …"
    docker cp "$HOST_CFG" "$CONTAINER:/tmp/seed-config.yml"
    docker exec "$CONTAINER" python3 /app/seed.py --config /tmp/seed-config.yml
    echo "✓ Seed done"
  fi
else
  echo "▶ Creating and starting $CONTAINER on port $PORT …"

  AZURE_MOUNT=()
  [[ -d "$AZURE_DIR" ]] && AZURE_MOUNT=(-v "$AZURE_DIR:/root/.azure")

  # Mount the roommate-sensei-o workspace so Copilot sessions can access
  # hil-stress scripts (skills/, roomboard-tests/, etc.) at /workspace
  WORKSPACE_MOUNT=()
  WORKSPACE_HOST="/Users/vn/ws/roommate-sensei-o"
  [[ -d "$WORKSPACE_HOST" ]] && WORKSPACE_MOUNT=(-v "$WORKSPACE_HOST:/workspace:ro")

  CONFIG_MOUNT=()
  SEED_ENVS=()
  if [[ -n "$SEED_CONFIG" ]]; then
    HOST_CFG="$(cd "$(dirname "$SEED_CONFIG")" && pwd)/$(basename "$SEED_CONFIG")"
    CONFIG_MOUNT=(-v "$HOST_CFG:/app/seed-config.yml:ro")
    SEED_ENVS+=(-e "GRU_SEED=1" -e "GRU_SEED_CONFIG=/app/seed-config.yml")
    if [[ -n "${GRU_GHE_TOKEN:-}" ]]; then
      SEED_ENVS+=(-e "GRU_GHE_TOKEN=${GRU_GHE_TOKEN}")
    fi
  fi

  docker run -d \
    --name "$CONTAINER" \
    -p "${PORT}:9400" \
    -v "${VOLUME}:/data" \
    "${AZURE_MOUNT[@]+"${AZURE_MOUNT[@]}"}" \
    "${WORKSPACE_MOUNT[@]+"${WORKSPACE_MOUNT[@]}"}" \
    "${CONFIG_MOUNT[@]+"${CONFIG_MOUNT[@]}"}" \
    "${SEED_ENVS[@]+"${SEED_ENVS[@]}"}" \
    "$IMAGE"
fi

echo "✓ gru-server running at http://localhost:${PORT}"
if [[ -n "$SEED_CONFIG" ]]; then
  echo "  Seeded from: $SEED_CONFIG"
  [[ -z "${GRU_GHE_TOKEN:-}" ]] && echo "  No token provided — authorize via Connectors page."
fi
