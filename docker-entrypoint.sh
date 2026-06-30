#!/bin/sh
# docker-entrypoint.sh — run optional seed then start gru-server
set -e

if [ "${GRU_SEED:-0}" = "1" ]; then
    echo "▶ Running seed…"
    python3 /app/seed.py
    echo "✓ Seed done"
fi

exec python3 -m server "$@"
