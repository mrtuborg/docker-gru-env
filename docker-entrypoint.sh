#!/bin/sh
# docker-entrypoint.sh — run optional seed then start gru-server
set -e

if [ "${GRU_SEED:-0}" = "1" ]; then
    echo "▶ Running seed…"
    python3 /app/seed.py
    echo "✓ Seed done"
fi

# Install gh copilot extension if not already present
if ! gh copilot --version >/dev/null 2>&1; then
    echo "▶ Installing gh copilot extension…"
    gh extension install github/gh-copilot 2>/dev/null || echo "  (gh copilot install skipped — will retry at runtime)"
fi

exec python3 -m server "$@"
