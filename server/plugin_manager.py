"""
Plugin Manager — registry, lifecycle, and health aggregation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Type

from .config import list_plugins, get_plugin
from .plugin_base import GruPlugin, PluginHealth, HealthStatus

logger = logging.getLogger(__name__)

# Registry: plugin_type → class
_REGISTRY: dict[str, Type[GruPlugin]] = {}


def register_plugin_type(cls: Type[GruPlugin]) -> Type[GruPlugin]:
    """Decorator to register a plugin class by its plugin_type."""
    _REGISTRY[cls.plugin_type.fget(None)] = cls  # type: ignore[attr-defined]
    return cls


def get_registered_types() -> dict[str, Type[GruPlugin]]:
    return dict(_REGISTRY)


class PluginManager:
    """
    Owns all active plugin instances.

    Instances are keyed by plugin_id (e.g. 'github-sensio').
    """

    def __init__(self) -> None:
        self._plugins: dict[str, GruPlugin] = {}
        self._health_cache: dict[str, PluginHealth] = {}
        self._health_task: asyncio.Task | None = None

    # ── Import plugin classes so they self-register ───────────────────────────

    @staticmethod
    def _import_plugins() -> None:
        from .plugins import github_plugin, copilot_plugin, azure_plugin, obsidian_plugin  # noqa: F401

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def load_all(self) -> None:
        """Load all plugin instances from the config DB."""
        self._import_plugins()
        rows = await list_plugins()
        for row in rows:
            if not row.get("enabled"):
                continue
            await self._instantiate(row["id"], row["plugin_type"], json.loads(row["config"]))
        logger.info("Loaded %d plugin(s) from DB", len(self._plugins))
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

    async def _instantiate(self, plugin_id: str, plugin_type: str, config: dict) -> GruPlugin | None:
        cls = _REGISTRY.get(plugin_type)
        if cls is None:
            logger.warning("Unknown plugin type %r — skipping %s", plugin_type, plugin_id)
            return None
        plugin = cls(plugin_id, config)
        try:
            await plugin.configure(config)
        except Exception as exc:
            logger.error("Plugin %s configure() failed: %s", plugin_id, exc)
        self._plugins[plugin_id] = plugin
        return plugin

    async def add_plugin(self, plugin_id: str, plugin_type: str, config: dict) -> GruPlugin:
        """Instantiate and start a new plugin (called from wizard/plugins API)."""
        from .config import upsert_plugin
        await upsert_plugin(plugin_id, plugin_type, config)
        plugin = await self._instantiate(plugin_id, plugin_type, config)
        if plugin is None:
            raise ValueError(f"Unknown plugin type: {plugin_type}")
        return plugin

    async def update_plugin(self, plugin_id: str, config: dict) -> GruPlugin:
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

    def get(self, plugin_id: str) -> GruPlugin | None:
        return self._plugins.get(plugin_id)

    def get_all(self) -> list[GruPlugin]:
        return list(self._plugins.values())

    def get_by_type(self, plugin_type: str) -> list[GruPlugin]:
        return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    # ── Health ────────────────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        """Poll all plugins for health every 30s."""
        while True:
            await asyncio.sleep(30)
            for plugin in list(self._plugins.values()):
                try:
                    self._health_cache[plugin.plugin_id] = await plugin.health()
                except Exception as exc:
                    self._health_cache[plugin.plugin_id] = PluginHealth(
                        status=HealthStatus.ERROR,
                        message=str(exc),
                    )

    def get_health(self, plugin_id: str) -> PluginHealth:
        return self._health_cache.get(
            plugin_id, PluginHealth(status=HealthStatus.UNKNOWN, message="Not yet checked")
        )

    def needs_setup(self) -> bool:
        """True if no plugins are configured (wizard not completed)."""
        return len(self._plugins) == 0
