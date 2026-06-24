"""
Azure Plugin — manages Azure Blob Storage tokens with auto-refresh.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from ..plugin_base import GruPlugin, PluginHealth, HealthStatus
from ..vault import load_secret, store_secret

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_INTERVAL = 50 * 60  # 50 minutes (matching bash daemon)
_TOKEN_FILE = Path(os.environ.get("AZURE_TOKEN_FILE", "/tmp/.azure-storage-token"))


class AzurePlugin(GruPlugin):

    def __init__(self, plugin_id: str, config: dict) -> None:
        super().__init__(plugin_id, config)
        self._refresh_task: asyncio.Task | None = None
        self._consecutive_failures = 0

    @property
    def plugin_type(self) -> str:
        return "azure"

    @property
    def display_name(self) -> str:
        return "Azure Storage"

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
            "required": ["auth_method"],
            "properties": {
                "auth_method": {
                    "type": "string",
                    "title": "Auth Method",
                    "enum": ["device_code_flow", "service_principal", "managed_identity"],
                    "default": "device_code_flow",
                },
                "storage_account": {"type": "string", "title": "Storage Account Name"},
                "container":       {"type": "string", "title": "Blob Container Name"},
                "tenant_id":       {"type": "string", "title": "Tenant ID (service principal)"},
                "client_id":       {"type": "string", "title": "Client ID (service principal)"},
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config
        # Restart the refresh daemon if already running
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        token = await load_secret(self.plugin_id, "access_token")
        if token:
            self._write_token_file(token)
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def health(self) -> PluginHealth:
        token = await load_secret(self.plugin_id, "access_token")
        if not token:
            return PluginHealth(HealthStatus.ERROR, "Not authenticated — authorize via device code flow")

        if self._consecutive_failures >= 3:
            return PluginHealth(
                HealthStatus.ERROR,
                "Token refresh failing — re-authorization required",
                {"consecutive_failures": self._consecutive_failures},
            )

        token_file = _TOKEN_FILE
        if token_file.exists():
            age_s = time.time() - token_file.stat().st_mtime
            if age_s > _TOKEN_REFRESH_INTERVAL + 600:
                return PluginHealth(HealthStatus.DEGRADED, "Token file stale — refresh may have failed")

        return PluginHealth(HealthStatus.HEALTHY, "Token active, auto-refresh running")

    async def teardown(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    # ── Token file ────────────────────────────────────────────────────────────

    def _write_token_file(self, token: str) -> None:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token)
        _TOKEN_FILE.chmod(0o600)

    # ── Auto-refresh loop ─────────────────────────────────────────────────────

    async def _refresh_loop(self) -> None:
        """Refresh token every 50 minutes. After 3 failures, set error state."""
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

    async def _refresh_token(self) -> str | None:
        """Attempt to refresh the Azure storage token using MSAL."""
        auth_method = self._config.get("auth_method", "device_code_flow")
        try:
            import msal
        except ImportError:
            logger.error("msal not installed — pip install msal")
            return None

        if auth_method == "service_principal":
            tenant_id = self._config.get("tenant_id", "")
            client_id = self._config.get("client_id", "")
            client_secret = await load_secret(self.plugin_id, "client_secret")
            if not all([tenant_id, client_id, client_secret]):
                return None
            app = msal.ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret,
            )
            result = app.acquire_token_for_client(
                scopes=["https://storage.azure.com/.default"]
            )
            return result.get("access_token")
        # device_code_flow tokens are not directly refreshable without a refresh_token;
        # return None to signal that re-auth is needed
        return None
