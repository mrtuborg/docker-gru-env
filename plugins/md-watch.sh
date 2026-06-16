#!/bin/bash
# File: /plugins/md-watch.sh
#
# Markdown Kanban watcher: drives Copilot sessions from an Obsidian Kanban
# board (.md file). For each OPEN card in the actionable column it runs a
# non-interactive Copilot session over the workspace.
#
#   md-watch run    <board.md> [--dir PATH] [--column NAME] [--dry-run] [--apply] [-- copilot-args...]
#   md-watch start  <board.md> [--dir PATH] [--column NAME] [--port N]  [--dry-run] [--apply] [-- ...]
#   md-watch stop   <board.md>
#   md-watch status <board.md>

md-watch_init() {
    register_plugin_command "md-watch" "md-watch" \
        "Markdown Kanban watcher" \
        "md-watch {run|start|stop|status} <board.md> [--dir PATH] [--column NAME] [--port N] [--dry-run] [--apply]"
}

# Derive a container name from the board file path.
_mdwatch_container_name() {
    local board="$1"
    local base
    base=$(basename "${board}" .md | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/-*$//')
    echo "gru-md-watcher-${base}"
}

# Resolve and validate board + workspace; sets $abs $ws $rel.
_mdwatch_resolve() {
    local board_arg="$1" ws_arg="$2"
    abs=$(cd "$(dirname "${board_arg}")" 2>/dev/null \
          && printf '%s/%s' "$(pwd)" "$(basename "${board_arg}")") \
        || { echo "ERROR: board file not found: ${board_arg}" >&2; return 1; }
    [ -f "${abs}" ] || { echo "ERROR: board file not found: ${abs}" >&2; return 1; }

    if [ -z "${ws_arg}" ]; then
        local cwd; cwd=$(pwd)
        case "${abs}" in
            "${cwd}"/*) ws="${cwd}" ;;
            *) ws=$(dirname "${abs}")
               echo "[cw] board is outside cwd; mounting only its directory (${ws}). Use --dir to widen." ;;
        esac
    else
        ws="${ws_arg}"
    fi
    ws=$(cd "${ws}" && pwd) || { echo "ERROR: workspace not found: ${ws}" >&2; return 1; }

    case "${abs}" in
        "${ws}"/*) ;;
        *) echo "ERROR: board (${abs}) must be inside workspace (${ws}). Use --dir to widen." >&2
           return 1 ;;
    esac
    rel="${abs#"${ws}"/}"
}

md-watch() {
    local subcmd="${1:-}"
    shift 2>/dev/null

    case "${subcmd}" in
        run|start)
            local board_file="" ws_arg="" port=9301
            local -a mw_args=()
            while [ "$#" -gt 0 ]; do
                case "$1" in
                    --dir)    [ -z "${2:-}" ] && { echo "ERROR: --dir requires a PATH" >&2; return 1; }
                              ws_arg="$2"; shift 2 ;;
                    --column) [ -z "${2:-}" ] && { echo "ERROR: --column requires a NAME" >&2; return 1; }
                              mw_args+=(--column "$2"); shift 2 ;;
                    --port)   port="$2"; shift 2 ;;
                    --dry-run|--apply) mw_args+=("$1"); shift ;;
                    --)       shift; mw_args+=(--); while [ "$#" -gt 0 ]; do mw_args+=("$1"); shift; done ;;
                    *)        if [ -z "${board_file}" ]; then board_file="$1"; else mw_args+=("$1"); fi
                              shift ;;
                esac
            done

            if [ -z "${board_file}" ]; then
                echo "Usage: md-watch ${subcmd} <board.md> [--dir PATH] [--column NAME]${subcmd:+ [--port N]} [--dry-run] [--apply]" >&2
                return 1
            fi

            local abs ws rel
            _mdwatch_resolve "${board_file}" "${ws_arg}" || return 1

            local extra
            extra=$(_cw_quote "/workspace/${rel}" "${mw_args[@]}")
            local cmd="${CW_COPILOT_BOOTSTRAP}; /tools/gru/scripts/md-watch.sh${extra}"

            if [ "${subcmd}" = "run" ]; then
                echo "[cw] md-watch run: ${abs}  (workspace: ${ws})"
                _cw_dock_cmd "${cmd}" "${ws}"
            else
                local cname
                cname="$(_mdwatch_container_name "${abs}")"
                if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                    echo "⚠️  md-watch already running (${cname})" >&2
                    return 1
                fi
                docker rm -f "${cname}" 2>/dev/null || true
                echo "🚀 Starting md-watch → ${cname}"
                _cw_dock_bg "${cmd}" "${ws}" "${cname}"
                echo "✅ md-watch started"
                _logui_start "${cname}" "${port}" "${cname}"
            fi
            ;;

        stop)
            local board_file="${1:-}"
            [ -z "${board_file}" ] && { echo "Usage: md-watch stop <board.md>" >&2; return 1; }
            local abs ws rel
            _mdwatch_resolve "${board_file}" "" || return 1
            local cname; cname="$(_mdwatch_container_name "${abs}")"

            local stopped=0
            if docker ps -q --filter "name=^${cname}$" | grep -q .; then
                echo "🛑 Stopping ${cname}..."
                docker stop "${cname}"
                docker rm "${cname}" 2>/dev/null || true
                stopped=1
            fi
            _logui_stop "${cname}"
            [ $stopped -eq 0 ] && echo "⚠️  md-watch not running (${cname})"
            ;;

        status)
            local board_file="${1:-}"
            [ -z "${board_file}" ] && { echo "Usage: md-watch status <board.md>" >&2; return 1; }
            local abs ws rel
            _mdwatch_resolve "${board_file}" "" || return 1
            local cname; cname="$(_mdwatch_container_name "${abs}")"

            echo "📊 md-watch: ${cname}"
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
            echo "Usage: md-watch {run|start|stop|status} <board.md> [options]"
            echo ""
            echo "  run     Run in foreground (interactive)"
            echo "  start   Run as background daemon + open dashboard in browser"
            echo "  stop    Stop background watcher (and dashboard)"
            echo "  status  Show watcher status and recent logs"
            echo ""
            echo "  <board.md>      Obsidian Kanban board file"
            echo "  --dir PATH      host workspace directory (default: board's directory)"
            echo "  --column NAME   column to process (default: Todo)"
            echo "  --port N        dashboard port for start (default: 9301)"
            echo "  --dry-run       show what would run without executing"
            echo "  --apply         mark processed cards done ([x]) in the board"
            return 1
            ;;
    esac
}
