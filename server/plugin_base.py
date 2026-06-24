"""
Plugin base class — all Gru Server plugins implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    HEALTHY  = "healthy"
    DEGRADED = "degraded"
    ERROR    = "error"
    UNKNOWN  = "unknown"


@dataclass
class PluginHealth:
    status:  HealthStatus
    message: str = ""
    details: dict = field(default_factory=dict)


class GruPlugin(ABC):
    """
    Base class for all Gru Server plugins.

    Lifecycle:
        __init__()   → called with plugin_id + stored config dict
        configure()  → called on setup or when config changes
        health()     → polled periodically (30s) for dashboard badges
        teardown()   → called on server shutdown or plugin disconnect
    """

    def __init__(self, plugin_id: str, config: dict) -> None:
        self.plugin_id = plugin_id
        self._config = config

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def plugin_type(self) -> str:
        """Short type string: 'github', 'copilot', 'azure', 'obsidian'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown in the UI."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description for the plugin card."""

    @property
    @abstractmethod
    def icon(self) -> str:
        """Lucide icon name (e.g. 'Github', 'Cloud', 'Bot')."""

    # ── Schema ────────────────────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def config_schema(cls) -> dict:
        """
        JSON Schema for the plugin's configuration form.
        Used by the wizard UI to render config fields.
        """

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def configure(self, config: dict) -> None:
        """Apply new configuration. Called after creation and on settings save."""

    @abstractmethod
    async def health(self) -> PluginHealth:
        """Return current health status. Must not raise."""

    @abstractmethod
    async def teardown(self) -> None:
        """Release resources — cancel tasks, close connections."""

    # ── Optional extensions ───────────────────────────────────────────────────

    def api_routes(self) -> list:
        """Return FastAPI APIRouter instances to mount under /api/plugins/{id}/."""
        return []

    async def handle_oauth_callback(self, code: str, state: str) -> dict:
        """Handle OAuth callback. Raise NotImplementedError if not supported."""
        raise NotImplementedError(f"{self.plugin_type} does not support OAuth callbacks")

    def to_dict(self) -> dict:
        """Serialize plugin metadata for API responses (no secrets)."""
        return {
            "id":           self.plugin_id,
            "plugin_type":  self.plugin_type,
            "display_name": self.display_name,
            "description":  self.description,
            "icon":         self.icon,
            "config":       self._config,
        }
