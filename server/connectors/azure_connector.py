"""
Azure Plugin — Azure Blob Storage via az CLI credentials.

Auth: DefaultAzureCredential (reads ~/.azure mounted at /root/.azure).
The plugin is only available when /root/.azure exists in the container.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..connector_base import GruConnector, ConnectorHealth, HealthStatus

logger = logging.getLogger(__name__)

_AZURE_DIR = Path("/root/.azure")


def azure_available() -> bool:
    """True when ~/.azure is mounted into the container."""
    return _AZURE_DIR.exists()


class AzureConnector(GruConnector):

    @property
    def connector_type(self) -> str:
        return "azure"

    @property
    def display_name(self) -> str:
        acct = self._config.get("storage_account", "")
        return f"Azure Storage ({acct})" if acct else "Azure Storage"

    @property
    def description(self) -> str:
        return "Azure Blob Storage via az CLI credentials (DefaultAzureCredential)"

    @property
    def icon(self) -> str:
        return "Cloud"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["storage_account"],
            "properties": {
                "storage_account": {"type": "string", "title": "Storage Account Name"},
                "container":       {"type": "string", "title": "Blob Container Name", "default": ""},
            },
        }

    async def auth_status(self) -> dict:
        if not azure_available():
            return {"has_token": False, "needs_auth": False, "unavailable": True,
                    "reason": "/root/.azure not mounted"}
        ok = await self._test_credential()
        return {"has_token": ok, "needs_auth": False}

    async def configure(self, config: dict) -> None:
        self._config = config

    async def health(self) -> ConnectorHealth:
        if not azure_available():
            return ConnectorHealth(
                HealthStatus.ERROR,
                "~/.azure not mounted — restart container with -v ~/.azure:/root/.azure:ro",
            )
        storage_account = self._config.get("storage_account", "")
        if not storage_account:
            return ConnectorHealth(HealthStatus.ERROR, "Storage account not configured")

        ok = await self._test_credential()
        if not ok:
            return ConnectorHealth(
                HealthStatus.ERROR,
                "az CLI credentials invalid or expired — run 'az login' on the host",
            )
        container = self._config.get("container", "")
        label = f"{storage_account}/{container}" if container else storage_account
        return ConnectorHealth(HealthStatus.HEALTHY, f"Connected to {label}")

    async def teardown(self) -> None:
        pass

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _test_credential(self) -> bool:
        """Try AzureCliCredential against the storage account."""
        storage_account = self._config.get("storage_account", "")
        container       = self._config.get("container", "artifacts")
        if not storage_account:
            return False
        try:
            import asyncio
            import subprocess, json as _json

            def _get_token() -> str:
                result = subprocess.run(
                    ["az", "account", "get-access-token",
                     "--resource", "https://storage.azure.com/",
                     "--query", "accessToken", "-o", "tsv"],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip())
                return result.stdout.strip()

            def _check():
                from azure.storage.blob import BlobServiceClient
                from azure.core.credentials import AccessToken
                import time

                token_str = _get_token()

                class _StaticCred:
                    def get_token(self, *scopes, **kwargs):
                        return AccessToken(token_str, int(time.time()) + 3600)

                client = BlobServiceClient(
                    f"https://{storage_account}.blob.core.windows.net",
                    credential=_StaticCred(),
                )
                cc = client.get_container_client(container)
                next(iter(cc.list_blobs(results_per_page=1)), None)
                return True

            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, _check),
                timeout=30,
            )
        except Exception as exc:
            logger.debug("Azure credential test failed: %s", exc)
            return False
