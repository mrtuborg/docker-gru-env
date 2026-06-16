#!/bin/bash
# File: /core/plugin_loader.sh
#
# Dynamically loads plugin scripts from the plugins directory and provides a
# command registration system for organizing functionality.

# Directory containing the plugins.
if [ -n "${SCRIPT_DIR}" ]; then
    PLUGINS_DIR="${SCRIPT_DIR}/plugins"
else
    PLUGINS_DIR="$(dirname "$(realpath "$0")")/../plugins"
fi

# Simple command tracking using a regular variable.
PLUGIN_COMMAND_LIST=""

# Register a plugin command (format: command:plugin:description).
register_plugin_command() {
    local plugin_name="$1"
    local command="$2"
    local help_text="$3"
    local description="${4:-$help_text}"

    if [ -z "$plugin_name" ] || [ -z "$command" ] || [ -z "$help_text" ]; then
        echo "ERROR: Invalid plugin command registration - missing required parameters" >&2
        return 1
    fi

    PLUGIN_COMMAND_LIST="${PLUGIN_COMMAND_LIST}${command}:${plugin_name}:${description}
"
}

# Show all available commands.
show_plugin_commands() {
    if [ -z "$PLUGIN_COMMAND_LIST" ]; then
        echo "No plugin commands registered."
        return 0
    fi

    local current_plugin=""
    local temp_file
    temp_file=$(mktemp)
    echo "$PLUGIN_COMMAND_LIST" | sort > "$temp_file"

    while IFS=':' read -r command plugin description; do
        if [ -n "$command" ]; then
            if [ "$plugin" != "$current_plugin" ]; then
                current_plugin="$plugin"
            fi
            printf " * %-25s - %s\n" "$command" "$description"
        fi
    done < "$temp_file"

    rm -f "$temp_file"
}

# Load all plugins from a single directory (helper used by load_plugins).
_load_plugins_from_dir() {
    local dir="$1"
    [ -d "$dir" ] || return 0
    echo "Loading plugins from: $dir"
    for plugin in "$dir"/*.sh; do
        [ -f "$plugin" ] || continue
        local plugin_name
        plugin_name=$(basename "$plugin" .sh)
        echo "  Loading plugin: $plugin_name"
        if source "$plugin"; then
            if typeset -f "${plugin_name}_init" >/dev/null 2>&1; then
                "${plugin_name}_init"
            fi
        else
            echo "  ERROR: Failed to load plugin $plugin_name"
        fi
    done
}

# Load all plugins from the plugins directory.
load_plugins() {
    if [ ! -d "$PLUGINS_DIR" ]; then
        echo "Plugins directory not found: $PLUGINS_DIR"
        return 1
    fi

    _load_plugins_from_dir "$PLUGINS_DIR"

    # When running as a git submodule, also load consumer plugins from the
    # parent repo's plugins/ directory (e.g. <consumer-repo>/plugins/).
    local consumer_plugins="${SCRIPT_DIR}/../plugins"
    if [ -d "$consumer_plugins" ]; then
        _load_plugins_from_dir "$(realpath "$consumer_plugins")"
    fi

    echo "Plugin loading complete."
}

# Check if a command is handled by a plugin.
is_plugin_command() {
    local command="$1"
    echo "$PLUGIN_COMMAND_LIST" | grep -q "^${command}:"
}
