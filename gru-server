#!/usr/bin/env bash
# gru-server — manage the gru-server-dev container
#
# Usage:
#   ./gru-server status               # running? port? uptime?
#   ./gru-server start                # start (create if needed)
#   ./gru-server start --port PORT    # bind to PORT (auto-recreates container if port changed)
#   ./gru-server stop                 # graceful stop
#   ./gru-server restart              # stop + start
#   ./gru-server logs                 # tail container logs
#   ./gru-server wipe                 # ⚠ remove container AND volume, then recreate
#   ./gru-server rebuild              # rebuild image + wipe + recreate
#
# Flags (only apply when creating a new container):
#   --port PORT      bind server to PORT on the host (default: 9400 / $GRU_PORT)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

CONTAINER="gru-server-dev"
IMAGE="gru-server:latest"
VOLUME="gru-data"
DEFAULT_PORT="${GRU_PORT:-9400}"
HOST_PORT=""   # set by --port

DB_CONTAINER="gru-analytics-db"
DB_NETWORK="gru-network"
DB_URL="postgresql://gru@${DB_CONTAINER}:5432/gru_analytics"

AZURE_DIR="$HOME/.azure"
WORKSPACE_HOST="/Users/vn/ws/roommate-sensei-o"
SKILLS_HOST="$REPO_ROOT/skills"   # repo skills dir — mapped into container as /app/skills

# ── arg parsing ───────────────────────────────────────────────────────────────
CMD="${1:-status}"
i=1
while [[ $i -le $# ]]; do
  arg="${!i}"
  case "$arg" in
    --port) i=$((i+1)); HOST_PORT="${!i}" ;;
  esac
  i=$((i+1))
done

# ── helpers ───────────────────────────────────────────────────────────────────
container_running() { docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q "true"; }
container_exists()  { docker inspect "$CONTAINER" &>/dev/null; }

find_free_port() {
  local p="$1"
  while lsof -iTCP:"$p" -sTCP:LISTEN -t &>/dev/null; do
    echo "  port $p in use, trying $((p+1)) …" >&2
    (( p++ ))
  done
  echo "$p"
}

do_status() {
  if ! container_exists; then
    echo "$CONTAINER  ✗ not created"
    echo "  Run: ./gru-server.sh start"
    return
  fi
  local state; state=$(docker inspect --format '{{.State.Status}}' "$CONTAINER")
  if container_running; then
    local since; since=$(docker inspect --format '{{.State.StartedAt}}' "$CONTAINER" | cut -c1-19 | tr 'T' ' ')
    local port; port=$(docker inspect --format '{{range $p,$v := .NetworkSettings.Ports}}{{if $v}}{{(index $v 0).HostPort}}{{end}}{{end}}' "$CONTAINER" 2>/dev/null)
    echo "$CONTAINER  ✓ running  (since ${since} UTC)"
    [[ -n "$port" ]] && echo "  http://localhost:$port"
  else
    echo "$CONTAINER  ○ $state"
  fi
}

do_start() {
  if container_running; then
    echo "✓ $CONTAINER already running"; do_status; return
  fi

  if container_exists; then
    if [[ -n "$HOST_PORT" ]]; then
      local mapped; mapped=$(docker inspect --format '{{range $p,$v := .HostConfig.PortBindings}}{{range $v}}{{.HostPort}} {{end}}{{end}}' "$CONTAINER" 2>/dev/null)
      if ! echo "$mapped" | grep -qw "$HOST_PORT"; then
        echo "▶ Recreating $CONTAINER with port $HOST_PORT (data volume preserved) …"
        docker rm -f "$CONTAINER" 2>/dev/null || true
        # fall through to create below
      else
        echo "▶ Starting $CONTAINER …"
        docker start "$CONTAINER"
        docker network connect "$DB_NETWORK" "$CONTAINER" 2>/dev/null || true
        do_status; return
      fi
    else
      echo "▶ Starting $CONTAINER …"
      docker start "$CONTAINER"
      docker network connect "$DB_NETWORK" "$CONTAINER" 2>/dev/null || true
      do_status; return
    fi
  fi

  # new container — pick port
  local port
  if [[ -n "$HOST_PORT" ]]; then
    port="$HOST_PORT"
  else
    port=$(find_free_port "$DEFAULT_PORT")
  fi

  echo "▶ Creating $CONTAINER on port $port …"

  local azure_args=();  [[ -d "$AZURE_DIR"      ]] && azure_args+=(-v "$AZURE_DIR:/root/.azure")
  local ws_args=();     [[ -d "$WORKSPACE_HOST" ]] && ws_args+=(-v "$WORKSPACE_HOST:/workspace:ro")
  local skills_args=(); [[ -d "$SKILLS_HOST"    ]] && skills_args+=(-v "$SKILLS_HOST:/app/skills:ro")

  docker run -d \
    --name "$CONTAINER" \
    --network "$DB_NETWORK" \
    -p "${port}:9400" \
    -v "${VOLUME}:/data" \
    -e "ANALYTICS_DB_URL=${DB_URL}" \
    "${azure_args[@]+"${azure_args[@]}"}" \
    "${ws_args[@]+"${ws_args[@]}"}" \
    "${skills_args[@]+"${skills_args[@]}"}" \
    "$IMAGE"

  echo "✓ http://localhost:$port"
}

do_stop() {
  if ! container_exists; then echo "✓ $CONTAINER not running"; return; fi
  echo "▶ Stopping $CONTAINER …"
  docker stop "$CONTAINER"
  echo "✓ Stopped"
}

do_wipe() {
  echo "⚠  Wiping $CONTAINER AND volume $VOLUME — all data will be lost!"
  docker rm -f "$CONTAINER" 2>/dev/null || true
  docker volume rm "$VOLUME"  2>/dev/null || true
  echo "✓ Wiped"
  do_start
}

do_rebuild() {
  echo "▶ Rebuilding $IMAGE …"
  docker build -f "$REPO_ROOT/Dockerfile.server" -t "$IMAGE" "$REPO_ROOT"
  echo "✓ Image built"
  do_wipe
}

# ── dispatch ──────────────────────────────────────────────────────────────────
case "$CMD" in
  status)  do_status ;;
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; do_start ;;
  logs)    docker logs -f "$CONTAINER" ;;
  wipe)    do_wipe ;;
  rebuild) do_rebuild ;;
  *)
    echo "Usage: $0 {status|start|stop|restart|logs|wipe|rebuild} [--port PORT]" >&2
    echo "  start --port PORT  — bind to PORT (auto-recreates container if port changed)" >&2
    echo "  wipe               — ⚠ remove container AND volume, recreate from scratch" >&2
    exit 1 ;;
esac
