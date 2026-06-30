#!/usr/bin/env bash
# Build the gru-server Docker image.
# Usage: ./server-build.sh [--no-cache]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
IMAGE="gru-server:latest"

echo "▶ Building $IMAGE …"
docker build "${@}" -f "$REPO_ROOT/Dockerfile.server" -t "$IMAGE" "$REPO_ROOT"
echo "✓ Build complete: $IMAGE"
