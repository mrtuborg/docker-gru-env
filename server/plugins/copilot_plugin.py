"""
Copilot Plugin — manages Copilot CLI sessions via Docker.
"""
from __future__ import annotations

import logging

from ..plugin_base import GruPlugin, PluginHealth, HealthStatus

logger = logging.getLogger(__name__)


class CopilotPlugin(GruPlugin):

    @property
    def plugin_type(self) -> str:
        return "copilot"

    @property
    def display_name(self) -> str:
        return "GitHub Copilot"

    @property
    def description(self) -> str:
        return "Interactive and automated Copilot CLI sessions with cost tracking"

    @property
    def icon(self) -> str:
        return "Bot"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "workspace_dir":      {"type": "string", "title": "Default Workspace Directory"},
                "skills_dir":         {"type": "string", "title": "Custom Skills Directory"},
                "extensions_dir":     {"type": "string", "title": "Extensions Directory (.github/extensions)"},
                "linked_repos":       {"type": "object", "title": "Linked Repos (name → URL)", "default": {}},
                "copilot_instructions": {
                    "type": "string",
                    "title": "Copilot Instructions",
                    "description": "Content for .github/copilot-instructions.md (merged with container defaults)",
                    "default": "",
                },
                "hooks": {
                    "type": "object",
                    "title": "Hooks (hooks.json)",
                    "default": {
                        "sessionEnd": {
                            "run": "python3 /tools/gru/src/cost-sync.py",
                            "description": "Append cost record for closed session",
                        }
                    },
                },
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config

    async def health(self) -> PluginHealth:
        # Check Docker is available and image exists
        try:
            import docker as docker_sdk
            client = docker_sdk.from_env()
            client.ping()
        except Exception as exc:
            return PluginHealth(HealthStatus.ERROR, f"Docker unavailable: {exc}")

        image_name = "gru:local"
        try:
            client.images.get(image_name)
            return PluginHealth(HealthStatus.HEALTHY, f"Docker OK, image {image_name} present")
        except Exception:
            return PluginHealth(
                HealthStatus.DEGRADED,
                f"Image {image_name} not built — run 'source ./gru' first",
            )

    async def teardown(self) -> None:
        pass
