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

# Build a merged copilot-instructions.md on the host and mount it read-only
# inside the container at /workspace/.github/copilot-instructions.md.
# This makes Path 1 (gh-watch bind-mount) identical to Path 2 (entrypoint
# fresh-clone), without touching the host workspace file.
#
# Sets _GHWATCH_MERGED_INSTR_FILE and appends the mount to CW_EXTRA_DOCKER_FLAGS.
# Caller must restore _saved_extra_docker after the docker call.
_ghwatch_merge_instructions() {
    local _ws="$1"
    local _defaults="${SCRIPT_DIR}/docker/defaults/copilot-instructions.md"
    local _workspace_instr="${_ws}/.github/copilot-instructions.md"

    if [[ ! -f "$_defaults" ]]; then
        return 0  # no defaults to append
    fi

    local _merged
    _merged=$(mktemp /tmp/gru-merged-instructions.XXXXXX)

    if [[ -f "$_workspace_instr" ]]; then
        {
            cat "$_workspace_instr"
            printf '\n---\n'
            printf '<!-- CONTAINER DEFAULTS — lower priority than workspace rules above.\n'
            printf '     If any rule here conflicts with a workspace rule above, the WORKSPACE RULE wins. -->\n\n'
            cat "$_defaults"
        } > "$_merged"
    else
        cp "$_defaults" "$_merged"
    fi

    _GHWATCH_MERGED_INSTR_FILE="$_merged"
    CW_EXTRA_DOCKER_FLAGS="${CW_EXTRA_DOCKER_FLAGS:-} -v ${_merged}:/workspace/.github/copilot-instructions.md:ro"
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
    local _GHWATCH_MERGED_INSTR_FILE=""

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
            _ghwatch_merge_instructions "${ws}"
            _cw_dock_cmd \
                "${CW_COPILOT_BOOTSTRAP}; /tools/gru/scripts/watcher-run.sh ${CW_CONFIG_FLAG} --working-dir /workspace --log-dir /logs ${board_flag}${extra}" \
                "${ws}"
            local _rc=$?
            CW_CONFIG_FLAG="${_saved_config_flag}"
            CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
            [[ -n "$_GHWATCH_MERGED_INSTR_FILE" ]] && rm -f "$_GHWATCH_MERGED_INSTR_FILE"
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
            _ghwatch_merge_instructions "${ws}"

            local cname
            cname="$(_ghwatch_container_name "${ws}${dir_config_flag:+-$dir_config_flag}")"

            if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                echo "⚠️  Watcher already running (${cname})" >&2
                CW_CONFIG_FLAG="${_saved_config_flag}"
                CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
                [[ -n "$_GHWATCH_MERGED_INSTR_FILE" ]] && rm -f "$_GHWATCH_MERGED_INSTR_FILE"
                return 1
            fi
            docker rm -f "${cname}" 2>/dev/null || true

            local git_setup="gh auth setup-git --hostname ${GH_HOST:-github.com} 2>/dev/null || true"
            local cmd="${CW_COPILOT_BOOTSTRAP}; ${git_setup}; /tools/gru/scripts/watcher-run.sh ${CW_CONFIG_FLAG} --working-dir /workspace --log-dir /logs ${board_flag}${extra}"

            echo "🚀 Starting watcher → ${cname}"
            _cw_dock_bg "${cmd}" "${ws}" "${cname}"
            CW_CONFIG_FLAG="${_saved_config_flag}"
            CW_EXTRA_DOCKER_FLAGS="${_saved_extra_docker}"
            # Note: merged file kept alive — container reads it after start.
            # It will be cleaned up on next gh-watch invocation or shell exit.
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
