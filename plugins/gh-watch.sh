#!/bin/bash
# File: /plugins/gh-watch.sh
#
# GitHub board watcher: runs the Copilot CLI autopilot over a GitHub project
# board (one Copilot session per open issue), via scripts/watcher-run.sh
# inside the container.
#
#   gh-watch [DIR] {run|start|stop|status} [BOARD] [--dir PATH] [--port N] [extra...]
#
# DIR (optional) — name of a subdirectory inside the workspace that contains
#   a config.yml. Equivalent to setting --config /workspace/DIR/config.yml.
#   Example: gh-watch hil-stress start
#
# BOARD (a number) — shorthand for --project BOARD.
# --dir PATH — host workspace directory (default: PWD / CW_WORKSPACE).
# --port N   — log UI port for `start` (default: 9300).

gh-watch_init() {
    register_plugin_command "gh-watch" "gh-watch" \
        "GitHub board watcher" \
        "gh-watch [DIR] {run|start|stop|status} [BOARD] [--dir PATH] [--port N] [extra args...]"
}

# Derive a deterministic container name from the workspace path.
_ghwatch_container_name() {
    local ws="${1:-default}"
    local base
    base=$(basename "${ws}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/-*$//')
    echo "gru-watcher-${base}"
}

# Write the merged copilot-instructions.md into the gru-instructions named volume
# so it persists after the terminal is closed. A /tmp bind-mount would be deleted
# when the shell exits, breaking the running container.
#
# The file lives at /data/instructions/<safe-name>/copilot-instructions.md inside
# the volume (mounted at /data/instructions by _cw_dock_bg). At container startup
# CW_INSTRUCT_BOOTSTRAP (core/common.sh) copies it to
# /workspace/.github/copilot-instructions.md.
#
# Sets CW_INSTRUCT_VOL_PATH and appends -e CW_INSTRUCT_VOL_PATH to CW_EXTRA_DOCKER_FLAGS.
_ghwatch_merge_instructions() {
    local _ws="$1"
    local _defaults="${SCRIPT_DIR}/docker/defaults/copilot-instructions.md"
    local _workspace_instr="${_ws}/.github/copilot-instructions.md"

    if [[ ! -f "$_defaults" ]]; then
        echo "[gh-watch] WARNING: defaults not found at ${_defaults} — container instructions will be workspace-only" >&2
    fi

    # Build merged content in a temp directory first, then copy into the volume.
    # We mount a directory (not a bare file) to avoid macOS Docker Desktop silently
    # creating a directory at the target when it cannot bind-mount a plain file.
    local _tmpdir _tmp
    # Use $HOME for the temp dir — Docker Desktop always shares /Users on macOS,
    # whereas /tmp (→ /private/tmp) is not accessible inside containers.
    _tmpdir=$(mktemp -d "${HOME}/.cache/gru-merged-instructions.XXXXXX" 2>/dev/null \
              || mktemp -d "${HOME}/gru-merged-instructions.XXXXXX")
    _tmp="${_tmpdir}/copilot-instructions.md"

    if [[ -f "$_workspace_instr" && -f "$_defaults" ]]; then
        {
            cat "$_workspace_instr"
            printf '\n---\n'
            printf '<!-- CONTAINER DEFAULTS — lower priority than workspace rules above.\n'
            printf '     If any rule here conflicts with a workspace rule above, the WORKSPACE RULE wins. -->\n\n'
            cat "$_defaults"
        } > "$_tmp"
    elif [[ -f "$_workspace_instr" ]]; then
        cp "$_workspace_instr" "$_tmp"
    elif [[ -f "$_defaults" ]]; then
        cp "$_defaults" "$_tmp"
    else
        rm -rf "$_tmpdir"
        return 0  # nothing to mount
    fi

    # Derive a stable key from the workspace path so concurrent watchers don't
    # overwrite each other's file in the shared volume.
    # Derive a collision-resistant key: basename + short hash of canonical path.
    local _safe_name _hash
    _safe_name=$(basename "${_ws}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/-*$//')
    _hash=$(printf '%s' "$(cd "${_ws}" 2>/dev/null && pwd || echo "${_ws}")" \
            | md5sum 2>/dev/null | cut -c1-8 \
            || printf '%s' "${_ws}" | cksum | awk '{print $1}')
    local _vol_path="/data/instructions/${_safe_name}-${_hash}/copilot-instructions.md"

    # Copy the merged file into the named volume via a one-shot container.
    # Mount the temp directory (not the file directly) so Docker Desktop on macOS
    # always gets a directory bind-mount, which it handles reliably.
    # Docker auto-creates the named volume on first use if it doesn't exist yet.
    if ! docker run --rm \
        -v "${CW_INSTRUCT_VOLUME}:/data/instructions" \
        -v "${_tmpdir}:/src:ro" \
        alpine:3.19 sh -c "
            mkdir -p /data/instructions/${_safe_name}-${_hash}
            cp /src/copilot-instructions.md ${_vol_path}
        " >/dev/null; then
        echo "[gh-watch] WARNING: failed to write instructions to volume — aborting" >&2
        rm -rf "$_tmpdir"
        return 1
    fi
    echo "[gh-watch] Merged instructions written to volume (${_vol_path})"

    rm -rf "$_tmpdir"

    # Pass the volume-internal path to the container — CW_INSTRUCT_BOOTSTRAP reads it.
    # The volume itself is already mounted at /data/instructions by _cw_dock_bg.
    export CW_INSTRUCT_VOL_PATH="${_vol_path}"
    CW_EXTRA_DOCKER_FLAGS="${CW_EXTRA_DOCKER_FLAGS:-} -e CW_INSTRUCT_VOL_PATH"
    export CW_EXTRA_DOCKER_FLAGS
}

# Parse common flags shared by run/start.
# Sets: ws, board_flag, extra (quoted remaining args).
# Consumes from positional args passed in.
_ghwatch_parse_run_args() {
    ws="${CW_WORKSPACE:-$PWD}"
    board_flag=""
    extra=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dir)
                if [ -z "${2:-}" ]; then
                    echo "ERROR: --dir requires a PATH" >&2
                    return 1
                fi
                ws="$2"; shift 2 ;;
            --port) shift 2 ;;   # consumed by start; ignored here
            *)
                if [[ "${1}" =~ ^[0-9]+$ ]] && [ -z "${board_flag}" ]; then
                    board_flag="--project $1"; shift
                else
                    extra="${extra} $(_cw_quote "$1")"; shift
                fi
                ;;
        esac
    done
}

gh-watch() {
    local subcmd="${1:-}"
    shift 2>/dev/null

    # Optional [DIR] prefix: if the first arg is not a known subcommand, treat
    # it as a config subdirectory name inside the workspace.
    # Example: gh-watch hil-stress start  →  subcmd=start, dir=hil-stress
    local dir_config_flag=""
    case "${subcmd}" in
        run|start|stop|status|"") ;;
        *)
            local _dir="${subcmd}"
            subcmd="${1:-}"; shift 2>/dev/null
            # Will be applied once we know the workspace path (in each case block).
            dir_config_flag="${_dir}"
            ;;
    esac

    # Save the current config flag and extra docker flags so overrides don't
    # pollute the shell session after this call returns.
    local _saved_config_flag="${CW_CONFIG_FLAG}"
    local _saved_extra_docker="${CW_EXTRA_DOCKER_FLAGS:-}"
    local _saved_instruct_vol_path="${CW_INSTRUCT_VOL_PATH:-}"

    # Helper: validate dir and set CW_CONFIG_FLAG for this invocation only.
    # Caller must restore _saved_config_flag on all exit paths.
    _ghwatch_apply_dir() {
        local _ws="$1"
        if [ -n "${dir_config_flag}" ]; then
            local _cfg="${_ws}/${dir_config_flag}/config.yml"
            if [ ! -f "${_cfg}" ]; then
                echo "ERROR: config not found: ${_cfg}" >&2
                return 1
            fi
            CW_CONFIG_FLAG="--config /workspace/${dir_config_flag}/config.yml"
        fi
    }

    case "${subcmd}" in
        run)
            local ws board_flag extra
            _ghwatch_parse_run_args "$@" || return 1
            _ghwatch_apply_dir "${ws}" || { CW_CONFIG_FLAG="${_saved_config_flag}"; return 1; }
            _ghwatch_merge_instructions "${ws}" || { CW_CONFIG_FLAG="${_saved_config_flag}"; CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"; return 1; }
            # Use workspace-relative log dir when DIR is set — logs land directly on host.
            local _log_dir="/logs"
            [[ -n "${dir_config_flag}" ]] && _log_dir="/workspace/${dir_config_flag}/logs"
            _cw_dock_cmd \
                "${CW_COPILOT_BOOTSTRAP}; mkdir -p ${_log_dir}; /tools/gru/scripts/watcher-run.sh ${CW_CONFIG_FLAG} --working-dir /workspace --log-dir ${_log_dir} ${board_flag}${extra}" \
                "${ws}"
            local _rc=$?
            CW_CONFIG_FLAG="${_saved_config_flag}"
            CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
            CW_INSTRUCT_VOL_PATH="${_saved_instruct_vol_path}"
            return $_rc
            ;;

        start)
            local ws board_flag extra
            local port=9300
            local -a remaining=()
            while [[ $# -gt 0 ]]; do
                case "$1" in
                    --port) port="$2"; shift 2 ;;
                    *) remaining+=("$1"); shift ;;
                esac
            done
            set -- "${remaining[@]}"
            _ghwatch_parse_run_args "$@" || return 1
            _ghwatch_apply_dir "${ws}" || { CW_CONFIG_FLAG="${_saved_config_flag}"; return 1; }
            _ghwatch_merge_instructions "${ws}" || { CW_CONFIG_FLAG="${_saved_config_flag}"; CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"; return 1; }

            local cname
            cname="$(_ghwatch_container_name "${ws}${dir_config_flag:+-$dir_config_flag}")"

            if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                echo "⚠️  Watcher already running (${cname})" >&2
                CW_CONFIG_FLAG="${_saved_config_flag}"
                CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
                CW_INSTRUCT_VOL_PATH="${_saved_instruct_vol_path}"
                return 1
            fi
            docker rm -f "${cname}" 2>/dev/null || true

            local git_setup="gh auth setup-git --hostname ${GH_HOST:-github.com} 2>/dev/null || true"
            # Use workspace-relative log dir when DIR is set — logs land directly on host.
            local _log_dir="/logs"
            [[ -n "${dir_config_flag}" ]] && _log_dir="/workspace/${dir_config_flag}/logs"
            local cmd="${CW_COPILOT_BOOTSTRAP}; ${git_setup}; mkdir -p ${_log_dir}; /tools/gru/scripts/watcher-run.sh ${CW_CONFIG_FLAG} --working-dir /workspace --log-dir ${_log_dir} ${board_flag}${extra}"

            echo "🚀 Starting watcher → ${cname}"
            _cw_dock_bg "${cmd}" "${ws}" "${cname}"
            CW_CONFIG_FLAG="${_saved_config_flag}"
            CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
            CW_INSTRUCT_VOL_PATH="${_saved_instruct_vol_path}"
            echo "✅ Watcher started"
            local host_config=""
            [ -n "${dir_config_flag}" ] && host_config="${ws}/${dir_config_flag}/config.yml"
            _logui_start "${cname}" "${port}" "${cname}" "${host_config}"
            ;;

        stop)
            local ws="${CW_WORKSPACE:-$PWD}"
            if [ "${1:-}" = "--dir" ]; then ws="${2:-$ws}"; fi
            local cname
            cname="$(_ghwatch_container_name "${ws}${dir_config_flag:+-$dir_config_flag}")"

            local stopped=0
            if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                echo "🛑 Stopping ${cname}..."
                docker stop "${cname}"
                docker rm "${cname}" 2>/dev/null || true
                stopped=1
            fi
            _logui_stop "${cname}"
            [ $stopped -eq 0 ] && echo "⚠️  Watcher not running (${cname})"
            ;;

        status)
            local ws="${CW_WORKSPACE:-$PWD}"
            if [ "${1:-}" = "--dir" ]; then ws="${2:-$ws}"; fi
            local cname
            cname="$(_ghwatch_container_name "${ws}${dir_config_flag:+-$dir_config_flag}")"

            echo "📊 Watcher: ${cname}"
            if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                echo "✅ Running"
                docker ps --filter "name=^${cname}$" \
                    --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
            else
                echo "❌ Not running"
            fi
            _logui_status "${cname}"
            if docker ps -aq --filter "name=^${cname}$" | grep -q .; then
                echo ""
                echo "--- recent logs ---"
                docker logs --tail 20 "${cname}" 2>&1
            fi
            ;;

        *)
            echo "Usage: gh-watch [DIR] {run|start|stop|status} [BOARD] [--dir PATH] [--port N] [--dry-run] [extra args...]"
            echo ""
            echo "  DIR       optional subdir inside workspace with config.yml"
            echo "  run       run watcher in foreground (interactive)"
            echo "  start     run watcher as background daemon + open log UI in browser"
            echo "  stop      stop background watcher (and log UI)"
            echo "  status    show watcher status and recent logs"
            echo ""
            echo "  BOARD         shorthand for --project BOARD (number)"
            echo "  --dir PATH    host workspace directory (default: PWD / CW_WORKSPACE)"
            echo "  --port N      log UI port for start (default: 9300)"
            return 1
            ;;
    esac
}
