"""
GitHub Plugin — connects to GitHub / GHE for project board watching.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..plugin_base import GruPlugin, PluginHealth, HealthStatus
from ..vault import load_secret, store_secret

logger = logging.getLogger(__name__)


class GitHubPlugin(GruPlugin):

    @property
    def plugin_type(self) -> str:
        return "github"

    @property
    def display_name(self) -> str:
        host = self._config.get("host", "github.com")
        return f"GitHub ({host})"

    @property
    def description(self) -> str:
        return "Project board watcher, cost reporting, and Copilot session attribution"

    @property
    def icon(self) -> str:
        return "Github"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["host", "data_repo", "project_owner", "project_number"],
            "properties": {
                "host":            {"type": "string", "title": "GitHub Host", "default": "github.com"},
                "auth_method":     {"type": "string", "title": "Auth Method",
                                    "enum": ["oauth_device_flow", "personal_access_token"],
                                    "default": "oauth_device_flow"},
                "data_repo":       {"type": "string", "title": "Data Repo (owner/repo)"},
                "pages_repo":      {"type": "string", "title": "Pages Repo (owner/repo)", "default": ""},
                "pages_branch":    {"type": "string", "title": "Pages Branch", "default": "main"},
                "project_owner":   {"type": "string", "title": "Project Owner (org/user)"},
                "project_number":  {"type": "integer", "title": "Project Board Number"},
                "project_name":    {"type": "string", "title": "Project Name", "default": ""},
                "allowed_repos":   {"type": "array", "items": {"type": "string"},
                                    "title": "Allowed Repos", "default": []},
                "repo_aliases":    {"type": "object", "title": "Repo Aliases", "default": {}},
                "repo_projects":   {"type": "object", "title": "Repo → Project Map", "default": {}},
                # Advanced watcher settings
                "max_issues":               {"type": "integer", "default": 50},
                "max_per_issue":            {"type": "integer", "default": 3},
                "pause_between_sessions":   {"type": "integer", "default": 0},
                "poll_interval":            {"type": "integer", "default": 300},
                "stage_order":              {"type": "array", "items": {"type": "string"},
                                             "default": ["Todo", "In Progress"]},
                "queue_stages":             {"type": "array", "items": {"type": "string"}, "default": []},
                "prompts_dir":              {"type": "string", "default": ""},
                "model":                    {"type": "string", "default": ""},
                "models":                   {"type": "array", "default": []},
                "device_status_file":       {"type": "string", "default": ""},
                "prompt_template":          {"type": "string", "default": ""},
                "working_dir":              {"type": "string", "default": ""},
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config
        self._token: str | None = await load_secret(self.plugin_id, "token")

    async def health(self) -> PluginHealth:
        token = await load_secret(self.plugin_id, "token")
        if not token:
            return PluginHealth(HealthStatus.ERROR, "No token configured — connect via OAuth or PAT")

        host = self._config.get("host", "github.com")
        url = f"https://{host}/api/v3/user" if host != "github.com" else "https://api.github.com/user"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                login = resp.json().get("login", "?")
                return PluginHealth(HealthStatus.HEALTHY, f"Authenticated as @{login}")
            elif resp.status_code == 401:
                return PluginHealth(HealthStatus.ERROR, "Token expired or invalid — re-authorize")
            else:
                return PluginHealth(HealthStatus.DEGRADED, f"Unexpected status {resp.status_code}")
        except httpx.ConnectError:
            return PluginHealth(HealthStatus.ERROR, f"Cannot reach {host}")
        except Exception as exc:
            return PluginHealth(HealthStatus.ERROR, str(exc))

    async def teardown(self) -> None:
        pass  # No background tasks in base GitHub plugin

    # ── OAuth Device Flow ─────────────────────────────────────────────────────

    async def start_device_flow(self) -> dict:
        """
        Initiate GitHub OAuth device flow.
        Returns: { verification_uri, user_code, device_code, expires_in, interval }
        """
        host = self._config.get("host", "github.com")
        base = f"https://{host}" if host != "github.com" else "https://github.com"
        # Scopes needed: repo + project + read:org
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/login/device/code",
                headers={"Accept": "application/json"},
                data={"client_id": _get_client_id(host), "scope": "repo project read:org"},
            )
            resp.raise_for_status()
            return resp.json()

    async def poll_device_flow(self, device_code: str, interval: int = 5) -> str | None:
        """
        Poll for token. Returns token string when granted, None while pending.
        Raises on error (expired, denied).
        """
        host = self._config.get("host", "github.com")
        base = f"https://{host}" if host != "github.com" else "https://github.com"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id":   _get_client_id(host),
                    "device_code": device_code,
                    "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data = resp.json()

        error = data.get("error")
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            return None
        if error in ("expired_token", "access_denied"):
            raise RuntimeError(f"OAuth error: {error}")
        if "access_token" in data:
            token = data["access_token"]
            await store_secret(self.plugin_id, "token", token)
            return token
        return None

    def token(self) -> str | None:
        """Synchronous token accessor for docker_service."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(load_secret(self.plugin_id, "token"))
        except Exception:
            return None


def _get_client_id(host: str) -> str:
    """
    Return the OAuth App client_id for the given host.
    For GHE instances, users must register their own OAuth app and
    set GRU_GITHUB_{HOST}_CLIENT_ID in the environment.
    """
    import os
    env_key = f"GRU_GITHUB_{host.upper().replace('.', '_')}_CLIENT_ID"
    client_id = os.environ.get(env_key)
    if not client_id:
        # Fall back to a built-in client_id only for github.com
        if host == "github.com":
            client_id = os.environ.get("GRU_GITHUB_CLIENT_ID", "")
        if not client_id:
            raise ValueError(
                f"OAuth client_id not configured for {host}. "
                f"Set {env_key} environment variable or use PAT auth."
            )
    return client_id
