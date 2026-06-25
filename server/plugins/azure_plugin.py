"""
Azure Plugin — manages Azure Blob Storage tokens with auto-refresh.

Supports three auth methods:
  - az_cli: uses the local `az login` session (DefaultAzureCredential) — no secrets needed
  - service_principal: uses tenant_id + client_id + client_secret
  - device_code_flow: interactive browser auth (legacy)
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
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
            "required": ["auth_method", "storage_account"],
            "properties": {
                "auth_method": {
                    "type": "string",
                    "title": "Auth Method",
                    "enum": ["az_cli", "service_principal", "device_code_flow"],
                    "default": "az_cli",
                },
                "storage_account": {"type": "string", "title": "Storage Account Name"},
                "container":       {"type": "string", "title": "Blob Container Name"},
                "tenant_id":       {"type": "string", "title": "Tenant ID (service principal only)"},
                "client_id":       {"type": "string", "title": "Client ID (service principal only)"},
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config
        # Restart the refresh daemon if already running
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

        auth_method = config.get("auth_method", "az_cli")

        if auth_method == "az_cli":
            # Get token from az CLI immediately
            token = await self._get_az_cli_token()
            if token:
                self._write_token_file(token)
                self._refresh_task = asyncio.create_task(self._refresh_loop())
        else:
            token = await load_secret(self.plugin_id, "access_token")
            if token:
                self._write_token_file(token)
                self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def health(self) -> PluginHealth:
        auth_method = self._config.get("auth_method", "az_cli")
        storage_account = self._config.get("storage_account", "")

        if not storage_account:
            return PluginHealth(HealthStatus.ERROR, "Storage account not configured")

        if auth_method == "az_cli":
            # Check if az CLI is logged in
            token = await self._get_az_cli_token()
            if not token:
                return PluginHealth(HealthStatus.ERROR, "az CLI not logged in — run 'az login'")
            return PluginHealth(
                HealthStatus.HEALTHY,
                f"Using az CLI credentials for {storage_account}",
                {"auth_method": "az_cli", "storage_account": storage_account},
            )

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

    # ── az CLI token ──────────────────────────────────────────────────────────

    async def _get_az_cli_token(self) -> str | None:
        """Get a storage token from the local az CLI session."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "az", "account", "get-access-token",
                "--resource", "https://storage.azure.com",
                "--query", "accessToken", "-o", "tsv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and stdout.strip():
                return stdout.decode().strip()
            logger.warning("az CLI token failed: %s", stderr.decode().strip())
            return None
        except FileNotFoundError:
            logger.warning("az CLI not found in PATH")
            return None

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
        """Attempt to refresh the Azure storage token."""
        auth_method = self._config.get("auth_method", "az_cli")

        if auth_method == "az_cli":
            return await self._get_az_cli_token()

        if auth_method == "service_principal":
            try:
                import msal
            except ImportError:
                logger.error("msal not installed — pip install msal")
                return None

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

        # device_code_flow tokens are not directly refreshable without a refresh_token
        return None
