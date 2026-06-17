#!/usr/bin/env bash
# install-skills.sh — Copy docker-gru-env skills into the Copilot skills dir.
# Called automatically by entrypoint.sh at container start.
# Can also be run manually inside the container to refresh after a git pull.

set -euo pipefail

SKILLS_SRC="$(cd "$(dirname "$0")/skills" && pwd)"
SKILLS_DST="$HOME/.copilot/skills"

mkdir -p "$SKILLS_DST"

count=0
for skill_dir in "$SKILLS_SRC"/*/; do
  name="$(basename "$skill_dir")"
  dst="$SKILLS_DST/$name"
  rm -rf "$dst"
  cp -r "$skill_dir" "$dst"
  echo "  installed $name"
  (( count++ )) || true
done

echo ""
echo "✓ $count skill(s) installed from $SKILLS_SRC → $SKILLS_DST"
