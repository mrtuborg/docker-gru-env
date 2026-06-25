"""
Azure Plugin — manages Azure Blob Storage tokens with auto-refresh.

Auth:
  - browser:           Azure Device Code Flow (user signs in via microsoft.com/devicelogin)
  - service_principal:  tenant_id + client_id + client_secret (headless CI)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from ..plugin_base import GruPlugin, PluginHealth, HealthStatus
from ..vault import load_secret, store_secret

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_INTERVAL = 50 * 60  # 50 minutes
_TOKEN_FILE = Path(os.environ.get("AZURE_TOKEN_FILE", "/tmp/.azure-storage-token"))

# Well-known Azure CLI public client_id — used by az, azd, terraform, etc.
_AZURE_CLI_CLIENT_ID = "04b07795-a710-4e09-9b56-0d023a5d76cd"


class AzurePlugin(GruPlugin):

    def __init__(self, plugin_id: str, config: dict) -> None:
        super().__init__(plugin_id, config)
        self._refresh_task: Optional[asyncio.Task] = None
        self._consecutive_failures = 0

    @property
    def plugin_type(self) -> str:
        return "azure"

    @property
    def display_name(self) -> str:
        acct = self._config.get("storage_account", "")
        return f"Azure Storage ({acct})" if acct else "Azure Storage"

    @property
    def description(self) -> str:
        return "Azure Blob Storage access for firmware bundles with automatic token refresh"

    @property
    def icon(self) -> str:
        return "Cloud"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["storage_account"],
            "properties": {
                "auth_method": {
                    "type": "string",
                    "title": "Auth Method",
                    "enum": ["browser", "service_principal"],
                    "default": "browser",
                },
                "storage_account": {"type": "string", "title": "Storage Account Name"},
                "container":       {"type": "string", "title": "Blob Container Name"},
                "tenant_id": {
                    "type": "string", "title": "Tenant ID",
                    "description": "Required for service principal. For browser auth, 'organizations' is used.",
                    "showWhen": {"field": "auth_method", "value": "service_principal"},
                },
                "client_id": {
                    "type": "string", "title": "Client ID",
                    "showWhen": {"field": "auth_method", "value": "service_principal"},
                },
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

        token = await load_secret(self.plugin_id, "access_token")
        if token:
            self._write_token_file(token)
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def health(self) -> PluginHealth:
        storage_account = self._config.get("storage_account", "")
        if not storage_account:
            return PluginHealth(HealthStatus.ERROR, "Storage account not configured")

        token = await load_secret(self.plugin_id, "access_token")
        if not token:
            return PluginHealth(
                HealthStatus.ERROR,
                "Not authenticated — click Authorize to sign in via browser",
                {"needs_auth": True},
            )

        if self._consecutive_failures >= 3:
            return PluginHealth(
                HealthStatus.ERROR,
                "Token refresh failing — re-authorization required",
                {"consecutive_failures": self._consecutive_failures, "needs_auth": True},
            )

        if _TOKEN_FILE.exists():
            age_s = time.time() - _TOKEN_FILE.stat().st_mtime
            if age_s > _TOKEN_REFRESH_INTERVAL + 600:
                return PluginHealth(HealthStatus.DEGRADED, "Token file stale — refresh may have failed")

        return PluginHealth(HealthStatus.HEALTHY, f"Token active for {storage_account}")

    async def teardown(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    # ── Token file ────────────────────────────────────────────────────────────

    def _write_token_file(self, token: str) -> None:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token)
        _TOKEN_FILE.chmod(0o600)

    # ── Azure Device Code Flow ────────────────────────────────────────────────

    async def start_device_flow(self) -> dict:
        """Start Azure AD device code flow for storage access."""
        tenant = self._config.get("tenant_id", "") or "organizations"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode",
                data={
                    "client_id": _AZURE_CLI_CLIENT_ID,
                    "scope": "https://storage.azure.com/.default offline_access",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return {
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "device_code": data["device_code"],
            "expires_in": data.get("expires_in", 900),
            "interval": data.get("interval", 5),
            "message": data.get("message", ""),
        }

    async def poll_device_flow(self, device_code: str, interval: int = 5) -> Optional[str]:
        """Poll Azure AD for token. Also stores the refresh_token for auto-refresh."""
        tenant = self._config.get("tenant_id", "") or "organizations"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "client_id": _AZURE_CLI_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data = resp.json()

        error = data.get("error")
        if error == "authorization_pending":
            return None
        if error == "slow_down":
            return None
        if error in ("expired_token", "authorization_declined"):
            raise RuntimeError(f"Azure auth error: {error}")
        if error:
            raise RuntimeError(f"Azure auth error: {error} — {data.get('error_description', '')}")

        access_token = data.get("access_token")
        if access_token:
            await store_secret(self.plugin_id, "access_token", access_token)
            self._write_token_file(access_token)
            if data.get("refresh_token"):
                await store_secret(self.plugin_id, "refresh_token", data["refresh_token"])
            if not self._refresh_task or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh_loop())
            return access_token
        return None

    # ── Auto-refresh loop ─────────────────────────────────────────────────────

    async def _refresh_loop(self) -> None:
        """Refresh token every 50 minutes."""
        while True:
            await asyncio.sleep(_TOKEN_REFRESH_INTERVAL)
            try:
                new_token = await self._refresh_token()
                if new_token:
                    await store_secret(self.plugin_id, "access_token", new_token)
                    self._write_token_file(new_token)
                    self._consecutive_failures = 0
                    logger.info("Azure token refreshed for %s", self.plugin_id)
                else:
                    self._consecutive_failures += 1
            except Exception as exc:
                self._consecutive_failures += 1
                logger.warning("Azure token refresh failed (%d): %s", self._consecutive_failures, exc)

    async def _refresh_token(self) -> Optional[str]:
        """Attempt to refresh the Azure storage token."""
        # Try refresh_token first (from device code flow)
        refresh_token = await load_secret(self.plugin_id, "refresh_token")
        if refresh_token:
            token = await self._refresh_with_rt(refresh_token)
            if token:
                return token

        auth_method = self._config.get("auth_method", "browser")
        if auth_method == "service_principal":
            return await self._refresh_service_principal()

        return None

    async def _refresh_with_rt(self, refresh_token: str) -> Optional[str]:
        """Use refresh_token to get a new access_token."""
        tenant = self._config.get("tenant_id", "") or "organizations"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                    data={
                        "client_id": _AZURE_CLI_CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": "https://storage.azure.com/.default offline_access",
                    },
                )
                data = resp.json()
            if "access_token" in data:
                if data.get("refresh_token"):
                    await store_secret(self.plugin_id, "refresh_token", data["refresh_token"])
                return data["access_token"]
        except Exception as exc:
            logger.warning("Refresh token exchange failed: %s", exc)
        return None

    async def _refresh_service_principal(self) -> Optional[str]:
        """Refresh via service principal credentials."""
        tenant_id = self._config.get("tenant_id", "")
        client_id = self._config.get("client_id", "")
        client_secret = await load_secret(self.plugin_id, "client_secret")
        if not all([tenant_id, client_id, client_secret]):
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "client_credentials",
                        "scope": "https://storage.azure.com/.default",
                    },
                )
                data = resp.json()
            return data.get("access_token")
        except Exception as exc:
            logger.warning("Service principal refresh failed: %s", exc)
            return None
