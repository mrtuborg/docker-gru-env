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
        # In server mode, check if gh copilot CLI is available
        import asyncio
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "copilot", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                version = stdout.decode().strip().split('\n')[0]
                return PluginHealth(HealthStatus.HEALTHY, f"Copilot CLI available ({version})")
        except FileNotFoundError:
            pass

        # Fallback: check Docker (submodule mode)
        try:
            import docker as docker_sdk
            client = docker_sdk.from_env()
            client.ping()
            image_name = "gru:local"
            try:
                client.images.get(image_name)
                return PluginHealth(HealthStatus.HEALTHY, f"Docker OK, image {image_name} present")
            except Exception:
                return PluginHealth(HealthStatus.DEGRADED, f"Image {image_name} not built")
        except Exception:
            pass

        return PluginHealth(HealthStatus.ERROR, "Neither gh copilot CLI nor Docker available")

    async def teardown(self) -> None:
        pass
