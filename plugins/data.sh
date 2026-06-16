#!/bin/bash
# File: /plugins/data.sh
#
# Data plugin: mirror session cost JSONL into the attributions database.
#
# Inside the container the Copilot CLI and the sessionEnd hook write cost
# records to COPILOT_DATA_HOME (/data/copilot) on the named data volume. The
# attributions database also lives on that volume (/data/copilot/attributions.db).
# `data update` mirrors the JSONL sessions into that DB by running cost-link,
# which upserts one attribution record per session. The repo mount is read-only,
# so the DB must live on the writable volume — never in the repo copy.

data_init() {
    register_plugin_command "data" "data" \
        "Data mirror interface" \
        "data {update|preview} [args] - mirror session cost JSONL into the attributions DB on the data volume"
}

data() {
    local subcmd="${1:-}"
    shift 2>/dev/null

    case "${subcmd}" in
        update)
            # Mirror JSONL -> DB on the data volume (writes attributions).
            local extra
            extra=$(_cw_quote "$@")
            _cw_dock_cmd \
                "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && python3 src/cost-link.py --apply ${CW_DB_FLAG} ${CW_CONFIG_FLAG}${extra}"
            ;;
        preview)
            # Dry-run: show what would be mirrored, without writing the DB.
            local extra
            extra=$(_cw_quote "$@")
            _cw_dock_cmd \
                "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && python3 src/cost-link.py ${CW_DB_FLAG} ${CW_CONFIG_FLAG}${extra}"
            ;;
        *)
            echo "Usage: data {update|preview} [args]"
            echo "  update   mirror JSONL sessions into the attributions DB on the data volume"
            echo "  preview  show what would be mirrored (no DB writes)"
            return 1
            ;;
    esac
}
