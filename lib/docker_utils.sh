#!/bin/bash
# File: /lib/docker_utils.sh
#
# Common docker execution + volume utilities shared across plugins.
# Mirrors docker-yocto-env/lib/docker_utils.sh.

# ---------------------------------------------------------------------------
# Auto-populate GH_TOKEN from the host gh keyring if not already set.
# gh auth token returns the token for the active account without exposing it
# in process args. This lets containers authenticate even when the host uses
# macOS Keychain / git-credential-manager rather than a raw env var.
# ---------------------------------------------------------------------------
_ensure_gh_token() {
    if [ -z "${GH_TOKEN:-}" ]; then
        local _host="${GH_HOST:-github.com}"
        local _tok
        _tok=$(GH_HOST="$_host" gh auth token 2>/dev/null)
        if [ -n "$_tok" ]; then
            export GH_TOKEN="$_tok"
        else
            echo "[cw] WARNING: GH_TOKEN not set and 'gh auth token' returned nothing." >&2
            echo "[cw]          Run: gh auth login --hostname $_host" >&2
        fi
    fi
}

# ---------------------------------------------------------------------------
# Obtain a short-lived Azure Storage access token from the host's authenticated
# az session and export it as AZURE_STORAGE_TOKEN.  The container has no az
# binary, so it cannot call DefaultAzureCredential's AzureCliCredential path.
# Instead hil-download-bundles.sh reads this env var and builds a lightweight
# StaticTokenCredential.  Token is valid for ~1h (Azure default).
# ---------------------------------------------------------------------------
_ensure_azure_token() {
    # Always refresh from az when available — a cached token may be expired.
    if command -v az >/dev/null 2>&1 && az account show >/dev/null 2>&1; then
        local _tok
        _tok=$(az account get-access-token \
                 --resource https://storage.azure.com/ \
                 --query accessToken -o tsv 2>/dev/null)
        if [ -n "$_tok" ]; then
            export AZURE_STORAGE_TOKEN="$_tok"
        else
            echo "[cw] WARNING: az is authenticated but get-access-token failed." >&2
        fi
    elif [ -z "${AZURE_STORAGE_TOKEN:-}" ]; then
        echo "[cw] WARNING: AZURE_STORAGE_TOKEN not set and az is not available/logged in." >&2
        echo "[cw]          Bundle downloads will fail. Run: az login" >&2
    fi
}

# Ensure the docker-gru-env image exists, building it from docker/Dockerfile
# when missing. Pass FORCE_BUILD=true (or `source ./gru --rebuild`) to rebuild.
_ensure_cw_image() {
    if [[ "${FORCE_BUILD:-false}" != "true" ]] && \
       docker image inspect "${CW_IMAGE}" >/dev/null 2>&1; then
        echo "Image ${CW_IMAGE} already exists, skipping build"
        return 0
    fi

    if [ ! -f "${CW_DOCKERFILE}" ]; then
        print_error "Dockerfile not found: ${CW_DOCKERFILE}"
        return 1
    fi

    echo "Building image ${CW_IMAGE} from ${CW_DOCKERFILE}..."
    docker build -t "${CW_IMAGE}" -f "${CW_DOCKERFILE}" "${PROJECT_TOP}" || {
        print_error "Failed to build image ${CW_IMAGE}"
        return 1
    }
}

# Create the named Docker volumes if they do not already exist.
_ensure_cw_volumes() {
    local vol
    for vol in "${CW_DATA_VOLUME}" "${CW_LOGS_VOLUME}" "${CW_INSTRUCT_VOLUME}"; do
        if ! docker volume inspect "${vol}" >/dev/null 2>&1; then
            echo "Creating volume: ${vol}"
            docker volume create "${vol}" >/dev/null || {
                print_error "Failed to create volume: ${vol}"
                return 1
            }
        fi
    done
}

# Ensure host overlay directories exist so bind-mounts don't get root-created.
_ensure_cw_dirs() {
    mkdir -p "${PROJECT_TOP}/data" "${PROJECT_TOP}/docs"
}

# Seed the data volume (COPILOT_DATA_HOME=/data/copilot) from the committed
# data/ snapshot on first run, so `cost report` shows existing history. Never
# overwrites files that already exist in the volume (live data wins).
_seed_cw_data() {
    [ -d "${PROJECT_TOP}/data" ] || return 0
    if ! docker run --rm \
        -v "${CW_DATA_VOLUME}:/data/copilot" \
        -v "${PROJECT_TOP}/data:/seed:ro" \
        alpine:latest sh -c '
            for f in cost-log.jsonl cost-log-historical.jsonl attributions.db; do
                if [ ! -e "/data/copilot/$f" ] && [ -f "/seed/$f" ]; then
                    cp "/seed/$f" "/data/copilot/$f"
                    echo "Seeded /data/copilot/$f from committed data/"
                fi
            done
        '; then
        echo "[cw] WARNING: failed to seed data volume from data/ (cost reports may show no history)" >&2
    fi
    # Non-fatal: a failed seed must not block environment initialization.
    return 0
}

# Shared docker run helper.
#
# Usage: _run_cw_docker <interactive:true|false> <command-string> [host_workspace_dir]
#
# Runs <command-string> inside the docker-gru-env image with the standard
# bind-mounts and named volumes. The image ENTRYPOINT is overridden with bash
# so arbitrary commands can be executed. When a host workspace dir is given it
# is mounted read-write at /workspace and becomes the working directory.
# Returns the container exit code.
_run_cw_docker() {
    command_exists docker || {
        print_error "docker not found in PATH"
        echo "Please install Docker Desktop or Colima:" >&2
        echo "  Docker Desktop: https://www.docker.com/products/docker-desktop" >&2
        echo "  Colima: brew install colima docker && colima start" >&2
        return 1
    }

    local interactive="$1"
    shift
    local cmd="$1"
    local workspace_dir="${2:-}"

    local -a tty_flags=()
    if [ "${interactive}" = "true" ]; then
        tty_flags+=("-it")
    fi

    _ensure_gh_token
    _ensure_azure_token

    local -a workspace_flags=()
    local workdir="/tools/gru"
    if [ -n "${workspace_dir}" ]; then
        workspace_dir=$(cd "${workspace_dir}" && pwd) || {
            print_error "workspace directory not found: ${workspace_dir}"
            return 1
        }
        workspace_flags=(-v "${workspace_dir}:/workspace:rw")
        workdir="/workspace"
    fi

    # Caller-supplied extra flags (e.g. additional -v mounts from consumer plugins).
    # NOTE: values are word-split on IFS, so paths with spaces are not supported.
    # For complex mounts use auto-mount logic below or extend _run_cw_docker directly.
    local -a extra_flags=()
    if [ -n "${CW_EXTRA_DOCKER_FLAGS:-}" ]; then
        read -r -a extra_flags <<< "${CW_EXTRA_DOCKER_FLAGS}"
    fi

    # Auto-mount host credentials that are commonly needed inside the container.
    [ -f "${HOME}/.gitconfig" ] && extra_flags+=(-v "${HOME}/.gitconfig:/root/.gitconfig:ro")

    docker run "${tty_flags[@]}" \
        -v "${PROJECT_TOP}:/tools/gru:ro" \
        -v "${PROJECT_TOP}/data:/tools/gru/data:rw" \
        -v "${PROJECT_TOP}/docs:/tools/gru/docs:rw" \
        "${workspace_flags[@]}" \
        -v "${CW_SSH_PATH}:/root/.ssh:ro" \
        -v "${CW_DATA_VOLUME}:/data/copilot" \
        -v "${CW_LOGS_VOLUME}:/logs" \
        -v "${CW_INSTRUCT_VOLUME}:/data/instructions" \
        "${extra_flags[@]}" \
        -e GH_TOKEN \
        -e GH_HOST \
        -e AZURE_STORAGE_TOKEN \
        -w "${workdir}" \
        --entrypoint /bin/bash \
        "${CW_IMAGE}" -lc "${cmd}"
}

# Thin wrappers mirroring docker-yocto-env's _poky_dock / _poky_dock_cmd.
#
#   _cw_dock     <cmd> [host_workspace_dir]   # interactive (-it)
#   _cw_dock_cmd <cmd> [host_workspace_dir]   # non-interactive
_cw_dock() {
    _run_cw_docker true "$1" "${2:-}"
}

_cw_dock_cmd() {
    _run_cw_docker false "$1" "${2:-}"
}

# Run the container detached (background daemon) with a fixed name so it can be
# managed by start/stop/status lifecycle commands.
#
# Usage: _cw_dock_bg <cmd> [host_workspace_dir] [container_name]
#
# The container is NOT started with --rm so docker logs/stop work after it
# exits. The caller is responsible for cleanup (docker rm).
_cw_dock_bg() {
    command_exists docker || {
        print_error "docker not found in PATH"
        return 1
    }

    local cmd="$1"
    local workspace_dir="${2:-}"
    local container_name="${3:-gru-watcher}"

    _ensure_gh_token
    _ensure_azure_token

    local -a workspace_flags=()
    local workdir="/tools/gru"
    if [ -n "${workspace_dir}" ]; then
        workspace_dir=$(cd "${workspace_dir}" && pwd) || {
            print_error "workspace directory not found: ${workspace_dir}"
            return 1
        }
        workspace_flags=(-v "${workspace_dir}:/workspace:rw")
        workdir="/workspace"
    fi

    # Caller-supplied extra flags. NOTE: word-split on IFS — paths with spaces not supported.
    local -a extra_flags=()
    if [ -n "${CW_EXTRA_DOCKER_FLAGS:-}" ]; then
        read -r -a extra_flags <<< "${CW_EXTRA_DOCKER_FLAGS}"
    fi

    # Auto-mount host credentials that are commonly needed inside the container.
    [ -f "${HOME}/.gitconfig" ] && extra_flags+=(-v "${HOME}/.gitconfig:/root/.gitconfig:ro")

    docker run -d --name "${container_name}" \
        -v "${PROJECT_TOP}:/tools/gru:ro" \
        -v "${PROJECT_TOP}/data:/tools/gru/data:rw" \
        -v "${PROJECT_TOP}/docs:/tools/gru/docs:rw" \
        "${workspace_flags[@]}" \
        -v "${CW_SSH_PATH}:/root/.ssh:ro" \
        -v "${CW_DATA_VOLUME}:/data/copilot" \
        -v "${CW_LOGS_VOLUME}:/logs" \
        -v "${CW_INSTRUCT_VOLUME}:/data/instructions" \
        "${extra_flags[@]}" \
        -e GH_TOKEN \
        -e GH_HOST \
        -e AZURE_STORAGE_TOKEN \
        -w "${workdir}" \
        --entrypoint /bin/bash \
        "${CW_IMAGE}" -lc "${cmd}" >/dev/null
}

# ---------------------------------------------------------------------------
# Log UI lifecycle helpers — shared by gh-watch and md-watch plugins.
#
#   _logui_pid_file  <name>   → /tmp path for the python PID
#   _logui_port_file <name>   → /tmp path for the port number
#   _logui_start     <name> <port> <container> [config_path]
#   _logui_stop      <name>
#   _logui_status    <name>
# ---------------------------------------------------------------------------

_logui_pid_file()  { echo "/tmp/gru-logui-${1}.pid"; }
_logui_port_file() { echo "/tmp/gru-logui-${1}.port"; }

_logui_start() {
    local name="$1" port="$2" container="$3" config_path="${4:-}"
    local log_ui_script="${SCRIPT_DIR}/docker/scripts/watch-log-ui.py"
    if [ ! -f "${log_ui_script}" ]; then
        echo "⚠️  Dashboard script not found: ${log_ui_script}" >&2
        return 0
    fi

    # Find a free port starting from the requested one. lsof is unreliable for
    # system-owned sockets on macOS; Python's bind probe is the ground truth.
    local actual_port
    actual_port=$(python3 - "$port" <<'PORTEOF'
import socket, sys
start = int(sys.argv[1])
for p in range(start, start + 20):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', p)); s.close(); print(p); break
    except OSError:
        s.close()
else:
    print(start)   # let Python report the error naturally
PORTEOF
)
    if [[ "$actual_port" != "$port" ]]; then
        echo "⚠️  Port ${port} in use — using ${actual_port} instead" >&2
    fi

    local -a ui_args=("${container}" "${actual_port}")
    [ -n "${config_path}" ] && ui_args+=(--config "${config_path}")
    python3 "${log_ui_script}" "${ui_args[@]}" &
    local _ui_pid=$!
    echo "${_ui_pid}"     > "$(_logui_pid_file "${name}")"
    echo "${actual_port}" > "$(_logui_port_file "${name}")"
    sleep 0.5

    # Verify the process is still alive — it may have crashed immediately.
    if ! kill -0 "${_ui_pid}" 2>/dev/null; then
        echo "⚠️  Dashboard failed to start on port ${actual_port}" >&2
        rm -f "$(_logui_pid_file "${name}")" "$(_logui_port_file "${name}")"
        return 1
    fi

    open "http://localhost:${actual_port}" 2>/dev/null || true
    echo "🌐 Dashboard → http://localhost:${actual_port}"
}

_logui_stop() {
    local name="$1"
    local pid_file
    pid_file="$(_logui_pid_file "${name}")"
    if [ -f "${pid_file}" ]; then
        local pid
        pid=$(cat "${pid_file}")
        kill "${pid}" 2>/dev/null && echo "🛑 Dashboard stopped (pid ${pid})"
        rm -f "${pid_file}" "$(_logui_port_file "${name}")"
    fi
}

_logui_status() {
    local name="$1"
    local pid_file port_file
    pid_file="$(_logui_pid_file "${name}")"
    port_file="$(_logui_port_file "${name}")"
    if [ -f "${pid_file}" ] && kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
        local port
        port=$(cat "${port_file}" 2>/dev/null || echo "?")
        echo "🌐 Dashboard → http://localhost:${port}"
    else
        echo "🌐 Dashboard not running  (use start to launch)"
    fi
}
