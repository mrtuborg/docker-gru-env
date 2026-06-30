"""
Copilot Connector — GitHub Copilot CLI sessions.

Auth: inherits the token from a linked GitHub connector (no separate OAuth).
Health: verifies gh + gh copilot are installed and the token is valid.
"""
from __future__ import annotations

import asyncio
import logging

from ..connector_base import GruConnector, ConnectorHealth, HealthStatus
from ..vault import load_secret

logger = logging.getLogger(__name__)


class CopilotConnector(GruConnector):

    @property
    def connector_type(self) -> str:
        return "copilot"

    @property
    def display_name(self) -> str:
        return "GitHub Copilot"

    @property
    def description(self) -> str:
        return "Copilot CLI sessions with cost tracking — shares auth with a GitHub connector"

    @property
    def icon(self) -> str:
        return "Bot"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "github_connector_id": {
                    "type": "string",
                    "title": "GitHub Connector ID",
                    "description": "ID of the GitHub connector to inherit auth from",
                },
                "working_dir": {"type": "string", "title": "Default Working Directory", "default": "/workspace"},
                "board_dir":   {"type": "string", "title": "Board Directory",           "default": "hil-stress"},
                "extensions_dir": {"type": "string", "title": "Extensions Directory"},
                "linked_repos":   {"type": "object", "title": "Linked Repos",           "default": {}},
                "watcher_stage_order":   {"type": "string", "default": "Todo, In progress"},
                "watcher_poll_interval": {"type": "integer", "default": 300},
                "watcher_max_issues":    {"type": "integer", "default": 50},
                "watcher_max_per_issue": {"type": "integer", "default": 3},
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config

    async def auth_status(self) -> dict:
        token = await self._get_github_token()
        return {"has_token": token is not None, "needs_auth": token is None}

    async def health(self) -> ConnectorHealth:
        # 1. Check gh CLI is available
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return ConnectorHealth(HealthStatus.ERROR, "gh CLI not available")
        except (FileNotFoundError, asyncio.TimeoutError):
            return ConnectorHealth(HealthStatus.ERROR, "gh CLI not installed in container")

        # 2. Check GitHub token inherited from linked connector
        token = await self._get_github_token()
        if not token:
            github_id = self._config.get("github_connector_id", "")
            hint = f" (linked: {github_id})" if github_id else " — set github_connector_id in config"
            return ConnectorHealth(
                HealthStatus.ERROR,
                f"No GitHub token{hint} — authenticate the GitHub connector first",
                {"needs_auth": True},
            )

        # 3. Check gh copilot is available (built-in since gh 2.x)
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "copilot", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**__import__("os").environ, "GH_TOKEN": token},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode == 0:
                version = stdout.decode().strip().split("\n")[0]
                return ConnectorHealth(HealthStatus.HEALTHY, f"Copilot CLI ready ({version})")
            # gh copilot is built-in in gh ≥ 2.x; if it fails, report the actual error
            err = (stdout.decode() + stderr.decode()).strip()
            return ConnectorHealth(HealthStatus.DEGRADED, err or "gh copilot unavailable")
        except asyncio.TimeoutError:
            return ConnectorHealth(HealthStatus.DEGRADED, "gh copilot --version timed out")

    async def teardown(self) -> None:
        pass

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _get_github_token(self) -> str | None:
        """Read token from the linked GitHub connector's vault slot."""
        github_id = self._config.get("github_connector_id", "")
        if not github_id:
            # Auto-discover: try common IDs
            for candidate in ("github-main", "github-0"):
                t = await load_secret(candidate, "token")
                if t:
                    return t
            return None
        return await load_secret(github_id, "token")
