#!/bin/bash
# File: /plugins/copilot.sh
#
# Copilot session plugin: interactive Copilot CLI + non-interactive single
# prompt, operating on host code mounted read-write at /workspace using the
# container's tooling.
#
#   copilot shell [--dir PATH]                  - interactive Copilot CLI
#   copilot run [--dir PATH] "prompt" [args...] - one non-interactive prompt
#
# The host directory mounted at /workspace defaults to the current directory,
# or $CW_WORKSPACE, or the explicit --dir PATH.

copilot_init() {
    register_plugin_command "copilot" "copilot" \
        "Copilot session interface" \
        "copilot {shell|run \"prompt\"} [--dir PATH] - interactive or non-interactive Copilot session over host code"
}

copilot() {
    local subcmd="${1:-}"
    shift 2>/dev/null

    # Resolve the host workspace. NOTE: this parsing must run in THIS shell (not a
    # command substitution / subshell) so the shift affects the caller's "$@".
    local ws="${CW_WORKSPACE:-$PWD}"
    if [ "${1:-}" = "--dir" ]; then
        if [ -z "${2:-}" ]; then
            echo "ERROR: --dir requires a PATH" >&2
            return 1
        fi
        ws="$2"
        shift 2
    fi

    case "${subcmd}" in
        shell)
            local extra
            extra=$(_cw_quote "$@")
            echo "[cw] Interactive Copilot CLI over: ${ws}"
            # Launch the Copilot CLI interactively inside the container.
            _cw_dock \
                "${CW_COPILOT_BOOTSTRAP}; cd /workspace && exec copilot${extra}" \
                "${ws}"
            ;;
        run)
            if [ "$#" -lt 1 ]; then
                echo "Usage: copilot run [--dir PATH] \"prompt\" [extra copilot args...]" >&2
                return 1
            fi
            local prompt="$1"
            shift
            local extra
            extra=$(_cw_quote "$@")
            echo "[cw] Non-interactive Copilot run over: ${ws}"
            # Single non-interactive prompt to the LLM (-p), then exit.
            _cw_dock_cmd \
                "${CW_COPILOT_BOOTSTRAP}; cd /workspace && copilot -p $(printf '%q' "${prompt}")${extra}" \
                "${ws}"
            ;;
        *)
            echo "Usage: copilot {shell|run \"prompt\"} [--dir PATH]"
            echo "  Operates on the host directory (default: current dir, or CW_WORKSPACE)"
            echo "  mounted read-write at /workspace inside the container."
            return 1
            ;;
    esac
}
