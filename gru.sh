#!/usr/bin/env bash
# gru.sh — manage gru-server-dev and gru-analytics-db containers
#
# Usage:
#   ./gru.sh status              # show status of both containers
#   ./gru.sh start               # start both (DB first, then server)
#   ./gru.sh stop                # stop both gracefully
#   ./gru.sh restart             # stop + start
#   ./gru.sh logs                # tail gru-server-dev logs
#   ./gru.sh logs db             # tail analytics DB logs
#   ./gru.sh db status           # DB container status only
#   ./gru.sh db start            # start DB only
#   ./gru.sh db stop             # stop DB only
#
# Flags:
#   --port PORT      bind gru-server to PORT on the host (default: 9400 / $GRU_PORT)
#   --db-port PORT   expose postgres to the host on PORT (default: not exposed)
#   --fresh          wipe volumes and recreate containers
#   --rebuild        rebuild the server image first, then --fresh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

SERVER_CONTAINER="gru-server-dev"
SERVER_IMAGE="gru-server:latest"
SERVER_VOLUME="gru-data"
SERVER_PORT="${GRU_PORT:-9400}"
SERVER_HOST_PORT=""   # overridden by --port

DB_CONTAINER="gru-analytics-db"
DB_VOLUME="gru-analytics-data"
DB_NETWORK="gru-network"
DB_HOST_PORT=""       # set by --db-port to expose postgres to host
DB_URL="postgresql://gru:gru@${DB_CONTAINER}:5432/gru_analytics"

AZURE_DIR="$HOME/.azure"
WORKSPACE_HOST="/Users/vn/ws/roommate-sensei-o"

# ── helpers ───────────────────────────────────────────────────────────────────

container_running() { docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null | grep -q "true"; }
container_exists()  { docker inspect "$1" &>/dev/null; }

status_line() {
  local name="$1"
  if ! container_exists "$name"; then
    echo "  $name  ✗ not created"
  elif container_running "$name"; then
    local started
    started=$(docker inspect --format '{{.State.StartedAt}}' "$name" | cut -c1-19 | tr 'T' ' ')
    local port_info=""
    if [[ "$name" == "$SERVER_CONTAINER" ]]; then
      port_info=$(docker inspect --format '{{range $p,$v := .NetworkSettings.Ports}}{{$p}}→{{(index $v 0).HostPort}} {{end}}' "$name" 2>/dev/null | tr -d ' ' | sed 's/9400\/tcp→/localhost:/')
      [[ -n "$port_info" ]] && port_info="  http://$port_info"
    fi
    echo "  $name  ✓ running  (since $started UTC)$port_info"
  else
    local status
    status=$(docker inspect --format '{{.State.Status}}' "$name")
    echo "  $name  ○ $status"
  fi
}

find_free_port() {
  local p="$1"
  while lsof -iTCP:"$p" -sTCP:LISTEN -t &>/dev/null; do
    echo "  port $p in use, trying $((p+1)) …" >&2
    (( p++ ))
  done
  echo "$p"
}

# ── db commands ───────────────────────────────────────────────────────────────

db_status() {
  echo "Analytics DB:"
  status_line "$DB_CONTAINER"
}

db_start() {
  docker network create "$DB_NETWORK" 2>/dev/null || true

  if container_running "$DB_CONTAINER"; then
    echo "✓ $DB_CONTAINER already running"
    return
  fi

  if container_exists "$DB_CONTAINER"; then
    echo "▶ Starting $DB_CONTAINER …"
    docker start "$DB_CONTAINER"
    docker network connect "$DB_NETWORK" "$DB_CONTAINER" 2>/dev/null || true
  else
    echo "▶ Creating $DB_CONTAINER …"
    local db_port_args=()
    if [[ -n "$DB_HOST_PORT" ]]; then
      db_port_args=(-p "${DB_HOST_PORT}:5432")
      echo "  Postgres exposed on host port $DB_HOST_PORT"
    fi
    docker run -d \
      --name "$DB_CONTAINER" \
      --network "$DB_NETWORK" \
      "${db_port_args[@]+"${db_port_args[@]}"}" \
      -v "${DB_VOLUME}:/var/lib/postgresql/data" \
      -e POSTGRES_USER=gru \
      -e POSTGRES_PASSWORD=gru \
      -e POSTGRES_DB=gru_analytics \
      postgres:16-alpine
  fi

  echo -n "  Waiting for postgres …"
  for i in $(seq 1 20); do
    if docker exec "$DB_CONTAINER" pg_isready -U gru -q 2>/dev/null; then
      echo " ready"
      return
    fi
    echo -n "."
    sleep 1
  done
  echo " timeout — check: docker logs $DB_CONTAINER"
}

db_stop() {
  if ! container_exists "$DB_CONTAINER"; then
    echo "✓ $DB_CONTAINER not running"
    return
  fi
  echo "▶ Stopping $DB_CONTAINER …"
  docker stop "$DB_CONTAINER"
  echo "✓ $DB_CONTAINER stopped"
}

db_fresh() {
  echo "▶ Wiping $DB_CONTAINER …"
  docker rm -f "$DB_CONTAINER" 2>/dev/null || true
  docker volume rm "$DB_VOLUME"  2>/dev/null || true
  echo "✓ DB wiped"
  db_start
}

# ── server commands ───────────────────────────────────────────────────────────

server_status() {
  echo "GRU Server:"
  status_line "$SERVER_CONTAINER"
}

server_start() {
  local fresh="${1:-0}"
  local rebuild="${2:-0}"

  if [[ $rebuild -eq 1 ]]; then
    echo "▶ Rebuilding $SERVER_IMAGE …"
    docker build -f "$REPO_ROOT/Dockerfile.server" -t "$SERVER_IMAGE" "$REPO_ROOT"
    echo "✓ Image built"
  fi

  if [[ $fresh -eq 1 ]]; then
    echo "▶ Wiping $SERVER_CONTAINER …"
    docker rm -f "$SERVER_CONTAINER" 2>/dev/null || true
    docker volume rm "$SERVER_VOLUME"  2>/dev/null || true
    echo "✓ Cleaned"
  fi

  if container_running "$SERVER_CONTAINER"; then
    echo "✓ $SERVER_CONTAINER already running"
    return
  fi

  if container_exists "$SERVER_CONTAINER"; then
    echo "▶ Starting existing $SERVER_CONTAINER …"
    docker start "$SERVER_CONTAINER"
    docker network connect "$DB_NETWORK" "$SERVER_CONTAINER" 2>/dev/null || true
  else
    local port
    if [[ -n "$SERVER_HOST_PORT" ]]; then
      port="$SERVER_HOST_PORT"
    else
      port=$(find_free_port "$SERVER_PORT")
    fi
    echo "▶ Creating $SERVER_CONTAINER on port $port …"

    local azure_args=()
    [[ -d "$AZURE_DIR" ]] && azure_args+=(-v "$AZURE_DIR:/root/.azure")

    local ws_args=()
    [[ -d "$WORKSPACE_HOST" ]] && ws_args+=(-v "$WORKSPACE_HOST:/workspace:ro")

    docker run -d \
      --name "$SERVER_CONTAINER" \
      --network "$DB_NETWORK" \
      -p "${port}:9400" \
      -v "${SERVER_VOLUME}:/data" \
      -e "ANALYTICS_DB_URL=${DB_URL}" \
      "${azure_args[@]+"${azure_args[@]}"}" \
      "${ws_args[@]+"${ws_args[@]}"}" \
      "$SERVER_IMAGE"

    echo "✓ $SERVER_CONTAINER → http://localhost:${port}"
  fi
}

server_stop() {
  if ! container_exists "$SERVER_CONTAINER"; then
    echo "✓ $SERVER_CONTAINER not running"
    return
  fi
  echo "▶ Stopping $SERVER_CONTAINER …"
  docker stop "$SERVER_CONTAINER"
  echo "✓ $SERVER_CONTAINER stopped"
}

# ── main dispatch ─────────────────────────────────────────────────────────────

CMD="${1:-status}"
SUB="${2:-}"
FRESH=0; REBUILD=0

i=1
while [[ $i -le $# ]]; do
  arg="${!i}"
  case "$arg" in
    --fresh)   FRESH=1 ;;
    --rebuild) REBUILD=1 ;;
    --port)
      i=$((i+1)); SERVER_HOST_PORT="${!i}" ;;
    --db-port)
      i=$((i+1)); DB_HOST_PORT="${!i}" ;;
  esac
  i=$((i+1))
done

case "$CMD" in
  status)
    server_status
    echo ""
    db_status
    ;;

  start)
    db_start
    echo ""
    server_start "$FRESH" "$REBUILD"
    ;;

  stop)
    server_stop
    echo ""
    db_stop
    ;;

  restart)
    server_stop
    db_stop 2>/dev/null || true
    echo ""
    db_start
    echo ""
    server_start 0 0
    ;;

  logs)
    if [[ "$SUB" == "db" ]]; then
      docker logs -f "$DB_CONTAINER"
    else
      docker logs -f "$SERVER_CONTAINER"
    fi
    ;;

  db)
    case "$SUB" in
      status)  db_status ;;
      start)   db_start ;;
      stop)    db_stop ;;
      fresh)   db_fresh ;;
      *)       echo "Usage: $0 db {status|start|stop|fresh}" >&2; exit 1 ;;
    esac
    ;;

  *)
    echo "Usage: $0 {status|start|stop|restart|logs [db]|db {status|start|stop|fresh}}" >&2
    echo "Flags: --fresh --rebuild --port PORT --db-port PORT" >&2
    echo "       --port PORT     bind gru-server to this host port (default: $SERVER_PORT)" >&2
    echo "       --db-port PORT  expose postgres to host on this port (default: not exposed)" >&2
    exit 1
    ;;
esac
