#!/bin/bash
# File: /plugins/cost.sh
#
# Cost reporting plugin: report, link, and dashboard subcommands.

cost_init() {
    register_plugin_command "cost" "cost" \
        "Cost reporting interface" \
        "cost {report|link|dashboard} [flags] - cost reports, attribution linking, dashboards"
}

cost() {
    local subcmd="${1:-}"
    shift 2>/dev/null

    case "${subcmd}" in
        report)
            local extra
            extra=$(_cw_quote "$@")
            _cw_dock_cmd \
                "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && python3 src/cost-report.py ${CW_CONFIG_FLAG} ${CW_DB_FLAG}${extra}"
            ;;
        link)
            local extra
            extra=$(_cw_quote "$@")
            _cw_dock_cmd \
                "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && python3 src/cost-link.py ${CW_CONFIG_FLAG} ${CW_DB_FLAG}${extra}"
            ;;
        dashboard)
            local extra
            extra=$(_cw_quote "$@")
            _cw_dock_cmd \
                "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && scripts/build-dashboard.sh ${CW_CONFIG_FLAG}${extra}"
            ;;
        *)
            echo "Usage: cost {report|link|dashboard} [flags]"
            return 1
            ;;
    esac
}
