#!/usr/bin/env bash
# Start the gru-server container.
#
# Usage:
#   ./server-run.sh              # start or restart existing container
#   ./server-run.sh --fresh      # remove existing container + volume, then start
#   ./server-run.sh --rebuild    # build image first, then --fresh start
#   ./server-run.sh --seed       # seed DB with HIL pipeline on first start (implies --fresh)
#
# Secrets:
#   GRU_GHE_TOKEN  — GHE PAT to store in vault during seed (optional; authorize via UI otherwise)
#
# The container is named  gru-server-dev  and listens on port 9400.
# Data is persisted in Docker volume  gru-data.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
IMAGE="gru-server:latest"
CONTAINER="gru-server-dev"
PORT="${GRU_PORT:-9400}"
VOLUME="gru-data"
AZURE_DIR="$HOME/.azure"

FRESH=0
REBUILD=0
SEED=0
for arg in "$@"; do
  case "$arg" in
    --fresh)   FRESH=1 ;;
    --rebuild) REBUILD=1; FRESH=1 ;;
    --seed)    SEED=1;    FRESH=1 ;;
  esac
done

# ── Rebuild image if requested ───────────────────────────────────────────────
if [[ $REBUILD -eq 1 ]]; then
  echo "▶ Rebuilding image …"
  docker build -f "$REPO_ROOT/Dockerfile.server" -t "$IMAGE" "$REPO_ROOT"
  echo "✓ Image built"
fi

# ── Remove existing container + volume if --fresh ────────────────────────────
if [[ $FRESH -eq 1 ]]; then
  echo "▶ Removing existing container and data volume …"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker volume rm "$VOLUME"  2>/dev/null || true
  echo "✓ Cleaned up"
fi

# ── Find a free port starting from PORT ──────────────────────────────────────
find_free_port() {
  local p="$1"
  while lsof -iTCP:"$p" -sTCP:LISTEN -t &>/dev/null; do
    echo "  port $p is in use, trying $((p+1)) …" >&2
    (( p++ ))
  done
  echo "$p"
}
PORT=$(find_free_port "$PORT")

# ── If container exists, just start it ───────────────────────────────────────
if docker inspect "$CONTAINER" &>/dev/null; then
  echo "▶ Starting existing container $CONTAINER …"
  docker start "$CONTAINER"
else
  # ── Otherwise create and run it ──────────────────────────────────────────
  echo "▶ Creating and starting $CONTAINER on port $PORT …"

  AZURE_MOUNT=()
  if [[ -d "$AZURE_DIR" ]]; then
    AZURE_MOUNT=(-v "$AZURE_DIR:/root/.azure")
  fi

  SEED_ENVS=()
  if [[ $SEED -eq 1 ]]; then
    SEED_ENVS+=(-e "GRU_SEED=1")
    if [[ -n "${GRU_GHE_TOKEN:-}" ]]; then
      SEED_ENVS+=(-e "GRU_GHE_TOKEN=${GRU_GHE_TOKEN}")
    fi
  fi

  docker run -d \
    --name "$CONTAINER" \
    -p "${PORT}:9400" \
    -v "${VOLUME}:/data" \
    "${AZURE_MOUNT[@]}" \
    "${SEED_ENVS[@]}" \
    "$IMAGE"
fi

echo "✓ gru-server running at http://localhost:${PORT}"
if [[ $SEED -eq 1 ]]; then
  echo "  Seeded with HIL stress-test pipeline (connector: ghe-roommate, pipeline: hil-stress)"
  echo "  If no token was provided, authorize via Connectors page after startup."
fi


# ── Rebuild image if requested ───────────────────────────────────────────────
if [[ $REBUILD -eq 1 ]]; then
  echo "▶ Rebuilding image …"
  docker build -f "$REPO_ROOT/Dockerfile.server" -t "$IMAGE" "$REPO_ROOT"
  echo "✓ Image built"
fi

# ── Remove existing container + volume if --fresh ────────────────────────────
if [[ $FRESH -eq 1 ]]; then
  echo "▶ Removing existing container and data volume …"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker volume rm "$VOLUME"  2>/dev/null || true
  echo "✓ Cleaned up"
fi

# ── Find a free port starting from PORT ──────────────────────────────────────
find_free_port() {
  local p="$1"
  while lsof -iTCP:"$p" -sTCP:LISTEN -t &>/dev/null; do
    echo "  port $p is in use, trying $((p+1)) …" >&2
    (( p++ ))
  done
  echo "$p"
}
PORT=$(find_free_port "$PORT")

# ── If container exists, just start it ───────────────────────────────────────
if docker inspect "$CONTAINER" &>/dev/null; then
  echo "▶ Starting existing container $CONTAINER …"
  docker start "$CONTAINER"
else
  # ── Otherwise create and run it ──────────────────────────────────────────
  echo "▶ Creating and starting $CONTAINER on port $PORT …"
  AZURE_MOUNT=()
  if [[ -d "$AZURE_DIR" ]]; then
    AZURE_MOUNT=(-v "$AZURE_DIR:/root/.azure")
  fi
  docker run -d \
    --name "$CONTAINER" \
    -p "${PORT}:9400" \
    -v "${VOLUME}:/data" \
    "${AZURE_MOUNT[@]}" \
    "${SEED_ENVS[@]}" \
    "$IMAGE"
fi

echo "✓ gru-server running at http://localhost:${PORT}"
