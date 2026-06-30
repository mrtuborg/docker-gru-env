#!/usr/bin/env bash
# Start the gru-server container.
#
# Usage:
#   ./server-run.sh            # start or restart existing container
#   ./server-run.sh --fresh    # remove existing container + volume, then start
#   ./server-run.sh --rebuild  # build image first, then --fresh start
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
for arg in "$@"; do
  case "$arg" in
    --fresh)   FRESH=1 ;;
    --rebuild) REBUILD=1; FRESH=1 ;;
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
  docker run -d \
    --name "$CONTAINER" \
    -p "${PORT}:9400" \
    -v "${VOLUME}:/data" \
    "${AZURE_MOUNT[@]}" \
    "$IMAGE"
fi

echo "✓ gru-server running at http://localhost:${PORT}"
