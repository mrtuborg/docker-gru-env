#!/bin/bash
# File: /core/common.sh
#
# Common utility functions for the Gru's Lab (copilot-workflow) environment.
# Docker execution + volume helpers live in lib/docker_utils.sh.

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to print error messages
print_error() {
    echo "ERROR: $1" >&2
}

# Function to print info messages
print_info() {
    echo "INFO: $1"
}

# Quote a list of arguments for safe re-use inside a `bash -lc` string.
# Echoes a leading-space-prefixed, %q-quoted concatenation of "$@".
_cw_quote() {
    local out=""
    local a
    for a in "$@"; do
        out+=" $(printf '%q' "$a")"
    done
    echo "$out"
}

# Shell snippet that authenticates the gh CLI inside the container using the
# host-provided GH_TOKEN, and exports GITHUB_TOKEN so the Copilot CLI can
# authenticate non-interactively. Warns (but does not fail) when GH_TOKEN is
# unset so read-only/offline commands can still run.
CW_AUTH_BOOTSTRAP='if [ -n "${GH_TOKEN:-}" ]; then export GITHUB_TOKEN="${GITHUB_TOKEN:-$GH_TOKEN}"; _cw_tok="$GH_TOKEN"; unset GH_TOKEN; echo "$_cw_tok" | gh auth login --hostname "${GH_HOST:-github.com}" --with-token >/dev/null 2>&1 || echo "[cw] WARNING: gh auth login failed" >&2; export GH_TOKEN="$_cw_tok"; unset _cw_tok; else echo "[cw] WARNING: GH_TOKEN not set; gh/copilot commands may fail" >&2; fi'

# The standalone copilot CLI is baked into the image, so the Copilot bootstrap is
# just authentication (kept as a separate name for the copilot/watcher plugins).
CW_COPILOT_BOOTSTRAP="${CW_AUTH_BOOTSTRAP}"
