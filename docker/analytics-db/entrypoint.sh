#!/usr/bin/env bash
# gru-analytics-db entrypoint — starts postgres (via the stock image entrypoint)
# and the analytics web dashboard side by side in one container.
#
# The web UI's lifecycle is tied 1:1 to this container: it comes up once
# postgres is ready and goes down when the container stops — independent
# of gru-server.
set -euo pipefail

# Start postgres exactly as the base image would, in the background.
docker-entrypoint.sh postgres &
PG_PID=$!

# Wait for postgres to accept connections before starting the web server.
until pg_isready -U "${POSTGRES_USER:-gru}" -q 2>/dev/null; do
  sleep 1
done

python3 /usr/local/bin/gru-analytics-web.py &
WEB_PID=$!

_shutdown() {
  kill -TERM "$WEB_PID" 2>/dev/null || true
  kill -TERM "$PG_PID"  2>/dev/null || true
  wait "$WEB_PID" 2>/dev/null || true
  wait "$PG_PID"  2>/dev/null || true
}
trap _shutdown TERM INT

# Exit (and let Docker's restart policy react) if either process dies.
wait -n "$PG_PID" "$WEB_PID"
_shutdown
