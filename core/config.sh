# File: /core/config.sh
#
# Configuration settings for the Gru's Lab (copilot-workflow) environment.

# Docker image built from docker/Dockerfile.
CW_IMAGE="${CW_IMAGE:-gru:local}"

# Named Docker volumes (persist Copilot data + logs across runs).
CW_DATA_VOLUME="${CW_DATA_VOLUME:-gru-data}"
CW_LOGS_VOLUME="${CW_LOGS_VOLUME:-gru-logs}"
# Stores the merged copilot-instructions.md for background watcher containers.
# Using a named volume (not /tmp) ensures the file survives terminal close.
CW_INSTRUCT_VOLUME="${CW_INSTRUCT_VOLUME:-gru-instructions}"

# Repository root (build context + bind-mount source).
#
# `gru` lives at the docker-gru-env repo root, so SCRIPT_DIR (set by the entry
# point) IS the repo root. Derive PROJECT_TOP from it rather than `git rev-parse`
# so that when used as a git submodule (e.g. roomboard-linux/copilot-workflow),
# the build context and bind-mounts point at copilot-workflow, not the parent repo.
if [ -n "${SCRIPT_DIR}" ]; then
    PROJECT_TOP="${SCRIPT_DIR}"
else
    # Fallback: this file is core/config.sh, so the repo root is one level up.
    PROJECT_TOP=$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]:-$0}")")/.." && pwd)
fi

# Dockerfile used to build the image.
CW_DOCKERFILE="${PROJECT_TOP}/docker/Dockerfile"

# Default GitHub Enterprise host (overridable from the host environment).
GH_HOST="${GH_HOST:-github.com}"

# Bridge host authentication into the container. The container has NO access to
# the host gh keyring or ~/.config/gh, so it authenticates with GH_TOKEN. When
# GH_TOKEN isn't already exported, derive it from the host's existing
# `gh auth login` so an already-authenticated host works transparently.
if [ -z "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
    GH_TOKEN=$(gh auth token --hostname "${GH_HOST}" 2>/dev/null || gh auth token 2>/dev/null || true)
    if [ -n "${GH_TOKEN}" ]; then
        export GH_TOKEN
        echo "[cw] Using GH_TOKEN derived from host 'gh auth' for ${GH_HOST}"
    fi
fi

# Path to the host SSH directory (mounted read-only for gh auth).
CW_SSH_PATH="${HOME}/.ssh"

# Container-side Copilot data home (the named data volume mount). Both the cost
# JSONL logs (written by the sessionEnd hook) and the attributions database live
# here, so they persist across runs and the DB stays writable (the repo mount is
# read-only).
CW_CONTAINER_DATA_HOME="/data/copilot"
CW_CONTAINER_DB="${CW_CONTAINER_DATA_HOME}/attributions.db"
CW_DB_FLAG="--db ${CW_CONTAINER_DB}"

# Auto-detect the workflow config in the tooling repo. When present, scripts
# that accept --config are invoked with its ABSOLUTE container path, so it
# resolves regardless of the working directory (e.g. watcher runs from
# /workspace, not the tooling repo).
CW_CONFIG_REL=".gru/config.yml"
if [ -f "${PROJECT_TOP}/${CW_CONFIG_REL}" ]; then
    CW_CONFIG_FLAG="--config /tools/gru/${CW_CONFIG_REL}"
else
    CW_CONFIG_FLAG=""
fi

export CW_IMAGE
export CW_DATA_VOLUME
export CW_LOGS_VOLUME
export CW_INSTRUCT_VOLUME
export PROJECT_TOP
export CW_DOCKERFILE
export GH_HOST
export CW_SSH_PATH
export CW_CONTAINER_DATA_HOME
export CW_CONTAINER_DB
export CW_DB_FLAG
export CW_CONFIG_REL
export CW_CONFIG_FLAG

# Enable Docker BuildKit for modern image building.
export DOCKER_BUILDKIT=1
