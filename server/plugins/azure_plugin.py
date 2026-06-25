"""
Azure Plugin — manages Azure Blob Storage access.

Auth:
  - sas_token:         Shared Access Signature (user generates from Azure Portal)
  - service_principal: tenant_id + client_id + client_secret (headless CI)
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
            "required": ["storage_account", "auth_method"],
            "properties": {
                "auth_method": {
                    "type": "string",
                    "title": "Auth Method",
                    "enum": ["sas_token", "service_principal"],
                    "default": "sas_token",
                },
                "storage_account": {"type": "string", "title": "Storage Account Name"},
                "subscription_id": {"type": "string", "title": "Subscription ID"},
                "resource_group":  {"type": "string", "title": "Resource Group"},
                "container":       {"type": "string", "title": "Blob Container Name", "default": ""},
                "sas_token": {
                    "type": "string", "title": "SAS Token",
                    "description": "Generate from Azure Portal → Storage Account → Shared access signature",
                    "showWhen": {"field": "auth_method", "value": "sas_token"},
                },
                "tenant_id": {
                    "type": "string", "title": "Tenant ID",
                    "showWhen": {"field": "auth_method", "value": "service_principal"},
                },
                "client_id": {
                    "type": "string", "title": "Client ID",
                    "showWhen": {"field": "auth_method", "value": "service_principal"},
                },
            },
        }

    async def auth_status(self) -> dict:
        """Return auth readiness for the wizard/dashboard."""
        auth_method = self._config.get("auth_method", "sas_token")
        if auth_method == "sas_token":
            sas = await load_secret(self.plugin_id, "sas_token")
            return {"has_token": sas is not None, "auth_method": "sas_token", "needs_auth": sas is None}
        else:
            secret = await load_secret(self.plugin_id, "client_secret")
            return {"has_token": secret is not None, "auth_method": "service_principal", "needs_auth": secret is None}

    async def configure(self, config: dict) -> None:
        self._config = config
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

        auth_method = config.get("auth_method", "sas_token")

        if auth_method == "sas_token":
            # SAS tokens are stored in config, no refresh needed
            sas = config.get("sas_token", "")
            if sas:
                await store_secret(self.plugin_id, "sas_token", sas)
        elif auth_method == "service_principal":
            token = await load_secret(self.plugin_id, "access_token")
            if token:
                self._write_token_file(token)
                self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def health(self) -> PluginHealth:
        storage_account = self._config.get("storage_account", "")
        if not storage_account:
            return PluginHealth(HealthStatus.ERROR, "Storage account not configured")

        auth_method = self._config.get("auth_method", "sas_token")

        if auth_method == "sas_token":
            sas = await load_secret(self.plugin_id, "sas_token")
            if not sas:
                return PluginHealth(
                    HealthStatus.ERROR,
                    "No SAS token — paste one from Azure Portal",
                    {"needs_auth": True},
                )
            # Validate by listing blobs (quick HEAD request)
            container = self._config.get("container", "")
            if container:
                try:
                    url = f"https://{storage_account}.blob.core.windows.net/{container}{sas}&restype=container&comp=list&maxresults=1"
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(url)
                    if resp.status_code == 200:
                        return PluginHealth(HealthStatus.HEALTHY, f"SAS token valid for {storage_account}/{container}")
                    elif resp.status_code == 403:
                        return PluginHealth(HealthStatus.ERROR, "SAS token expired or invalid permissions")
                    elif resp.status_code == 404:
                        return PluginHealth(HealthStatus.DEGRADED, f"Container '{container}' not found")
                except Exception as exc:
                    return PluginHealth(HealthStatus.DEGRADED, f"Cannot validate: {exc}")
            return PluginHealth(HealthStatus.HEALTHY, f"SAS token configured for {storage_account}")

        # service_principal
        token = await load_secret(self.plugin_id, "access_token")
        if not token:
            return PluginHealth(
                HealthStatus.ERROR,
                "Not authenticated — configure service principal credentials",
                {"needs_auth": True},
            )

        if self._consecutive_failures >= 3:
            return PluginHealth(
                HealthStatus.ERROR,
                "Token refresh failing — check service principal credentials",
                {"consecutive_failures": self._consecutive_failures, "needs_auth": True},
            )

        return PluginHealth(HealthStatus.HEALTHY, f"Token active for {storage_account}")

    async def teardown(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    # ── Token file ────────────────────────────────────────────────────────────

    def _write_token_file(self, token: str) -> None:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(token)
        _TOKEN_FILE.chmod(0o600)

    # ── Auto-refresh loop (service_principal only) ────────────────────────────

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
