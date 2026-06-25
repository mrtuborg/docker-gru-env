"""
Connector Manager — registry, lifecycle, and health aggregation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Type

from .config import list_plugins, get_plugin
from .connector_base import GruConnector, ConnectorHealth, HealthStatus

logger = logging.getLogger(__name__)

# Registry: connector type → class
_REGISTRY: dict[str, Type[GruConnector]] = {}


def register_plugin_type(cls: Type[GruConnector]) -> Type[GruConnector]:
    """Decorator to register a connector class by its connector_type."""
    _REGISTRY[cls.connector_type.fget(None)] = cls  # type: ignore[attr-defined]
    return cls


def get_registered_types() -> dict[str, Type[GruConnector]]:
    return dict(_REGISTRY)


class ConnectorManager:
    """
    Owns all active connector instances.

    Instances are keyed by connector ID (e.g. 'github-sensio').
    """

    def __init__(self) -> None:
        self._plugins: dict[str, GruConnector] = {}
        self._health_cache: dict[str, ConnectorHealth] = {}
        self._health_task: asyncio.Task | None = None

    # ── Import connector classes so they self-register ────────────────────────

    @staticmethod
    def _import_plugins() -> None:
        from .connectors import github_connector, copilot_connector, azure_connector, obsidian_connector  # noqa: F401

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def load_all(self) -> None:
        """Load all connector instances from the config DB."""
        self._import_plugins()
        rows = await list_plugins()
        for row in rows:
            if not row.get("enabled"):
                continue
            await self._instantiate(row["id"], row["plugin_type"], json.loads(row["config"]))
        logger.info("Loaded %d connector(s) from DB", len(self._plugins))
        self._health_task = asyncio.create_task(self._health_loop())

    async def teardown_all(self) -> None:
        if self._health_task:
            self._health_task.cancel()
        for plugin in list(self._plugins.values()):
            try:
                await plugin.teardown()
            except Exception as exc:
                logger.warning("teardown failed for %s: %s", plugin.plugin_id, exc)
        self._plugins.clear()

    # ── Instance management ───────────────────────────────────────────────────

    async def _instantiate(self, plugin_id: str, connector_type: str, config: dict) -> GruConnector | None:
        cls = _REGISTRY.get(connector_type)
        if cls is None:
            logger.warning("Unknown connector type %r — skipping %s", connector_type, plugin_id)
            return None
        plugin = cls(plugin_id, config)
        try:
            await plugin.configure(config)
        except Exception as exc:
            logger.error("Connector %s configure() failed: %s", plugin_id, exc)
        self._plugins[plugin_id] = plugin
        return plugin

    async def add_plugin(self, plugin_id: str, connector_type: str, config: dict) -> GruConnector:
        """Instantiate and start a new connector (called from wizard/plugins API)."""
        from .config import upsert_plugin
        await upsert_plugin(plugin_id, connector_type, config)
        plugin = await self._instantiate(plugin_id, connector_type, config)
        if plugin is None:
            raise ValueError(f"Unknown connector type: {connector_type}")
        return plugin

    async def update_plugin(self, plugin_id: str, config: dict) -> GruConnector:
        from .config import upsert_plugin, get_plugin as _get
        row = await _get(plugin_id)
        if row is None:
            raise KeyError(plugin_id)
        await upsert_plugin(plugin_id, row["plugin_type"], config)
        plugin = self._plugins[plugin_id]
        plugin._config = config
        await plugin.configure(config)
        return plugin

    async def remove_plugin(self, plugin_id: str) -> None:
        from .config import delete_plugin
        plugin = self._plugins.pop(plugin_id, None)
        if plugin:
            await plugin.teardown()
        await delete_plugin(plugin_id)
        self._health_cache.pop(plugin_id, None)

    def get(self, plugin_id: str) -> GruConnector | None:
        return self._plugins.get(plugin_id)

    def get_all(self) -> list[GruConnector]:
        return list(self._plugins.values())

    def get_by_type(self, connector_type: str) -> list[GruConnector]:
        return [p for p in self._plugins.values() if p.connector_type == connector_type]

    # ── Health ────────────────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        """Poll all connectors for health every 30s."""
        while True:
            await asyncio.sleep(30)
            for plugin in list(self._plugins.values()):
                try:
                    self._health_cache[plugin.plugin_id] = await plugin.health()
                except Exception as exc:
                    self._health_cache[plugin.plugin_id] = ConnectorHealth(
                        status=HealthStatus.ERROR,
                        message=str(exc),
                    )

    def get_health(self, plugin_id: str) -> ConnectorHealth:
        return self._health_cache.get(
            plugin_id, ConnectorHealth(status=HealthStatus.UNKNOWN, message="Not yet checked")
        )

    def needs_setup(self) -> bool:
        """True if no connectors are configured (wizard not completed)."""
        return len(self._plugins) == 0
