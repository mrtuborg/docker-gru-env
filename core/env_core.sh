#!/bin/bash
# File: /core/env_core.sh
#
# Core environment initialization for the Gru's Lab (copilot-workflow) env.

# Get the directory containing this core script.
if [ -n "${BASH_SOURCE[0]}" ]; then
    CORE_DIR=$(dirname "$(realpath "${BASH_SOURCE[0]}")")
else
    CORE_DIR=$(dirname "$(realpath "${(%):-%x}")")
fi

# Load common functions.
if ! source "${CORE_DIR}/common.sh"; then
    echo "ERROR: Failed to load common.sh" >&2
    return 1
fi

# Load configuration.
if ! source "${CORE_DIR}/config.sh"; then
    echo "ERROR: Failed to load config.sh" >&2
    return 1
fi

# Load shared docker utilities.
if ! source "${CORE_DIR}/../lib/docker_utils.sh"; then
    echo "ERROR: Failed to load docker_utils.sh" >&2
    return 1
fi

# Load the plugin loader.
if ! source "${CORE_DIR}/plugin_loader.sh"; then
    echo "ERROR: Failed to load plugin_loader.sh" >&2
    return 1
fi

# Initialize the environment.
_initialize_environment() {
    echo "Initializing docker-gru-env environment..."

    if ! command_exists docker; then
        print_error "docker not found in PATH"
        echo "Please install Docker Desktop or Colima:" >&2
        echo "  Docker Desktop: https://www.docker.com/products/docker-desktop" >&2
        echo "  Colima: brew install colima docker && colima start" >&2
        return 1
    fi

    echo "CW_IMAGE=${CW_IMAGE}"
    echo "PROJECT_TOP=${PROJECT_TOP}"

    _ensure_cw_image || return 1
    _ensure_cw_volumes || return 1
    _ensure_cw_dirs || return 1
    _seed_cw_data || return 1

    # Load plugins after the environment is ready.
    load_plugins

    echo "Environment initialization complete."
}
