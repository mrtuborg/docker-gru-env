"""
Obsidian MD-Kanban Plugin — watches .md kanban boards and drives Copilot sessions.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from ..connector_base import GruConnector, ConnectorHealth, HealthStatus

logger = logging.getLogger(__name__)

# Path to the existing md_kanban.py parser
_MD_KANBAN = Path(__file__).parents[2] / "src" / "md_kanban.py"


class ObsidianConnector(GruConnector):

    def __init__(self, plugin_id: str, config: dict) -> None:
        super().__init__(plugin_id, config)
        self._watcher_task: asyncio.Task | None = None

    @property
    def connector_type(self) -> str:
        return "obsidian"

    @property
    def display_name(self) -> str:
        board = Path(self._config.get("board_path", "")).name or "Obsidian Board"
        return f"Obsidian: {board}"

    @property
    def description(self) -> str:
        return "Watches an Obsidian Kanban board and runs Copilot sessions per card"

    @property
    def icon(self) -> str:
        return "FileText"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["board_path"],
            "properties": {
                "board_path":    {"type": "string", "title": "Kanban Board File (.md)"},
                "workspace_dir": {"type": "string", "title": "Workspace Directory"},
                "watch_column":  {"type": "string", "title": "Column to Watch", "default": ""},
                "poll_interval": {"type": "integer", "title": "Poll Interval (seconds)", "default": 300},
                "auto_mark_done": {"type": "boolean", "title": "Mark cards done after session", "default": False},
                "dry_run":        {"type": "boolean", "title": "Dry run (no execution)", "default": False},
            },
        }

    async def configure(self, config: dict) -> None:
        self._config = config

    async def health(self) -> ConnectorHealth:
        board_path = Path(self._config.get("board_path", ""))
        if not board_path:
            return ConnectorHealth(HealthStatus.ERROR, "No board file configured")
        if not board_path.exists():
            return ConnectorHealth(HealthStatus.ERROR, f"Board file not found: {board_path}")
        if not _MD_KANBAN.exists():
            return ConnectorHealth(HealthStatus.ERROR, "md_kanban.py not found in src/")

        # Quick parse check
        try:
            result = subprocess.run(
                [sys.executable, str(_MD_KANBAN), "columns", "--file", str(board_path)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return ConnectorHealth(HealthStatus.DEGRADED, f"Board parse error: {result.stderr.strip()}")
            columns = [c for c in result.stdout.strip().splitlines() if c]
            return ConnectorHealth(
                HealthStatus.HEALTHY,
                f"Board OK — {len(columns)} column(s): {', '.join(columns)}",
                {"columns": columns},
            )
        except Exception as exc:
            return ConnectorHealth(HealthStatus.ERROR, str(exc))

    async def teardown(self) -> None:
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()

    # ── Board access helpers (used by boards router) ───────────────────────────

    def list_columns(self) -> list[str]:
        board_path = self._config.get("board_path", "")
        if not board_path or not Path(board_path).exists():
            return []
        result = subprocess.run(
            [sys.executable, str(_MD_KANBAN), "columns", "--file", board_path],
            capture_output=True, text=True, timeout=5,
        )
        return [c for c in result.stdout.strip().splitlines() if c]

    def list_cards(self, column: str, include_done: bool = False) -> list[str]:
        board_path = self._config.get("board_path", "")
        if not board_path:
            return []
        args = [sys.executable, str(_MD_KANBAN), "list", "--file", board_path, "--column", column]
        if include_done:
            args.append("--all")
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
        return [c for c in result.stdout.split("\0") if c.strip()]
