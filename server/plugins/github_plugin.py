"""
GitHub Plugin — connects to GitHub / GHE for project board watching.

Auth strategy:
  1. Check vault for stored token (from prior Device Flow or PAT)
  2. OAuth Device Flow — user authorizes in browser
  3. For GHE without a client_id: GitHub App Manifest Flow auto-registers one
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

from ..plugin_base import GruPlugin, PluginHealth, HealthStatus
from ..runtime import server_url
from ..vault import load_secret, store_secret

logger = logging.getLogger(__name__)

_APP_CLIENT_ID_KEY = "app_client_id"
_APP_CLIENT_SECRET_KEY = "app_client_secret"


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
            "required": ["host", "project_owner", "project_number"],
            "properties": {
                "host":            {"type": "string", "title": "GitHub Host", "default": "github.com"},
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
        self._token: Optional[str] = await load_secret(self.plugin_id, "token")

    async def health(self) -> PluginHealth:
        token = await load_secret(self.plugin_id, "token")
        if not token:
            return PluginHealth(
                HealthStatus.ERROR,
                "Not authenticated — click Authorize to sign in via browser",
                {"needs_auth": True},
            )

        host = self._config.get("host", "github.com")
        url = f"https://{host}/api/v3/user" if host != "github.com" else "https://api.github.com/user"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                login = resp.json().get("login", "?")
                return PluginHealth(HealthStatus.HEALTHY, f"Authenticated as @{login}")
            elif resp.status_code == 401:
                return PluginHealth(HealthStatus.ERROR, "Token expired — re-authorize via browser")
            else:
                return PluginHealth(HealthStatus.DEGRADED, f"Unexpected status {resp.status_code}")
        except httpx.ConnectError:
            return PluginHealth(HealthStatus.ERROR, f"Cannot reach {host}")
        except Exception as exc:
            return PluginHealth(HealthStatus.ERROR, str(exc))

    async def teardown(self) -> None:
        pass

    # ── Auth status ───────────────────────────────────────────────────────────

    async def auth_status(self) -> dict:
        """Return current auth state: has_token, has_client_id, needs_manifest."""
        token = await load_secret(self.plugin_id, "token")
        client_id = await self._get_client_id()
        host = self._config.get("host", "github.com")
        return {
            "has_token": token is not None,
            "has_client_id": client_id is not None,
            "needs_manifest": client_id is None,
            "host": host,
        }

    # ── GitHub App Manifest Flow ──────────────────────────────────────────────

    def get_manifest(self) -> dict:
        """Build the GitHub App manifest JSON for auto-registration."""
        host = self._config.get("host", "github.com")
        base_url = server_url()
        return {
            "name": f"Gru Server ({host})",
            "url": base_url,
            "hook_attributes": {"url": "https://example.com", "active": False},
            "redirect_url": f"{base_url}/api/auth/github/manifest-callback",
            "callback_urls": [f"{base_url}/api/auth/github/callback"],
            "public": True,
            "request_oauth_on_install": True,
            "default_permissions": {
                "issues": "write",
                "pull_requests": "write",
                "contents": "read",
                "organization_projects": "admin",
            },
            "default_events": [],
        }

    async def complete_manifest_flow(self, code: str) -> dict:
        """
        Exchange the temporary code from manifest callback for app credentials.
        Stores client_id + client_secret in the vault, then enables device flow.
        """
        host = self._config.get("host", "github.com")
        api_base = f"https://{host}/api/v3" if host != "github.com" else "https://api.github.com"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{api_base}/app-manifests/{code}/conversions",
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        client_id = data.get("client_id", "")
        client_secret = data.get("client_secret", "")
        pem = data.get("pem", "")
        app_id = data.get("id")

        if not client_id:
            raise ValueError("GitHub did not return a client_id")

        await store_secret(self.plugin_id, _APP_CLIENT_ID_KEY, client_id)
        await store_secret(self.plugin_id, _APP_CLIENT_SECRET_KEY, client_secret)
        if pem:
            await store_secret(self.plugin_id, "app_pem", pem)

        # Enable device flow via PATCH /app (requires JWT auth as the app)
        if pem and app_id:
            try:
                await self._enable_device_flow(api_base, app_id, pem)
            except Exception as e:
                logger.warning("Could not auto-enable device flow: %s", e)

        logger.info("GitHub App registered for %s: client_id=%s…", host, client_id[:8])
        return {
            "app_id": app_id,
            "app_name": data.get("name"),
            "client_id": client_id,
            "html_url": data.get("html_url", ""),
        }

    async def _enable_device_flow(self, api_base: str, app_id: int, pem: str) -> None:
        """Generate JWT and PATCH /app to enable device_flow_enabled."""
        import jwt as pyjwt

        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + (10 * 60), "iss": str(app_id)}
        token = pyjwt.encode(payload, pem, algorithm="RS256")

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{api_base}/app",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"device_flow_enabled": True},
            )
            if resp.status_code < 300:
                logger.info("Device flow enabled for app %s", app_id)
            else:
                logger.warning("PATCH /app returned %d: %s", resp.status_code, resp.text)

    # ── OAuth Device Flow ─────────────────────────────────────────────────────

    async def _get_client_id(self) -> Optional[str]:
        """
        Resolve the OAuth client_id:
          1. Vault (from manifest flow registration)
          2. Environment variable GRU_GITHUB_{HOST}_CLIENT_ID
          3. Built-in for github.com
        """
        vault_cid = await load_secret(self.plugin_id, _APP_CLIENT_ID_KEY)
        if vault_cid:
            return vault_cid

        host = self._config.get("host", "github.com")
        env_key = f"GRU_GITHUB_{host.upper().replace('.', '_').replace('-', '_')}_CLIENT_ID"
        env_cid = os.environ.get(env_key)
        if env_cid:
            return env_cid

        if host == "github.com":
            return os.environ.get("GRU_GITHUB_CLIENT_ID")

        return None

    async def start_device_flow(self) -> dict:
        """Initiate GitHub OAuth device flow. Raises ValueError if no client_id."""
        client_id = await self._get_client_id()
        if not client_id:
            host = self._config.get("host", "github.com")
            raise ValueError(
                f"No OAuth client_id for {host}. "
                "Register a GitHub App first via the manifest flow."
            )

        client_secret = await load_secret(self.plugin_id, _APP_CLIENT_SECRET_KEY)
        host = self._config.get("host", "github.com")
        base = f"https://{host}" if host != "github.com" else "https://github.com"
        payload = {"client_id": client_id, "scope": "repo project read:org"}
        if client_secret:
            payload["client_secret"] = client_secret
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/login/device/code",
                headers={"Accept": "application/json"},
                data=payload,
            )
            if resp.status_code != 200:
                body = resp.text
                logger.error("Device flow start failed (%d): %s", resp.status_code, body)
                raise ValueError(f"GitHub rejected device flow ({resp.status_code}): {body}")
            return resp.json()

    async def poll_device_flow(self, device_code: str, interval: int = 5) -> Optional[str]:
        """Poll for token. Returns token when granted, None while pending."""
        client_id = await self._get_client_id()
        if not client_id:
            raise ValueError("No client_id — cannot poll device flow")

        client_secret = await load_secret(self.plugin_id, _APP_CLIENT_SECRET_KEY)
        host = self._config.get("host", "github.com")
        base = f"https://{host}" if host != "github.com" else "https://github.com"
        payload = {
            "client_id":   client_id,
            "device_code": device_code,
            "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
        }
        if client_secret:
            payload["client_secret"] = client_secret
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{base}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data=payload,
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
