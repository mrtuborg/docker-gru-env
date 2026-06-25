"""
Obsidian Sync connector — syncs a remote vault via the official obsidian-headless CLI
('ob') and reads Kanban boards from the synced local copy.

Auth: Obsidian account email + password, stored in the connector vault.
      Non-interactive login via: ob login --email ... --password ...
      Credentials are never written to disk directly; ob stores a session token
      in ~/.config/obsidian-headless/ inside the container.

Sync: ob sync-setup --vault <name> --path /vault/ob-<id> --mode pull-only
      ob sync --path /vault/ob-<id>   (called on demand / periodic)

Board reading: uses existing md_kanban.py parser on the synced local .md file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

from ..connector_base import GruConnector, ConnectorHealth, HealthStatus

logger = logging.getLogger(__name__)

_MD_KANBAN = Path(__file__).parents[2] / "src" / "md_kanban.py"
_OB = "ob"  # obsidian-headless CLI, installed via npm install -g obsidian-headless
_VAULT_BASE = Path("/vault")  # base dir for synced vaults inside the container


def _ob(*args: str, timeout: int = 30, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run the 'ob' CLI and return CompletedProcess."""
    return subprocess.run(
        [_OB, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input_text,
    )


class ObsidianConnector(GruConnector):

    def __init__(self, plugin_id: str, config: dict) -> None:
        super().__init__(plugin_id, config)
        self._sync_task: asyncio.Task | None = None

    @property
    def connector_type(self) -> str:
        return "obsidian"

    @property
    def display_name(self) -> str:
        vault = self._config.get("vault_name", "")
        return f"Obsidian: {vault}" if vault else "Obsidian Sync"

    @property
    def description(self) -> str:
        return "Syncs an Obsidian vault via Obsidian Sync and watches a Kanban board"

    @property
    def icon(self) -> str:
        return "FileText"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["email", "password", "vault_name", "board_path"],
            "properties": {
                "email":      {"type": "string", "title": "Obsidian Account Email"},
                "password":   {"type": "string", "title": "Obsidian Account Password", "secret": True},
                "vault_name": {"type": "string", "title": "Remote Vault Name"},
                "board_path": {"type": "string", "title": "Kanban Board File (relative to vault root)"},
                "watch_column":   {"type": "string", "title": "Column to Watch", "default": "Todo"},
                "poll_interval":  {"type": "integer", "title": "Sync Poll Interval (seconds)", "default": 300},
                "auto_mark_done": {"type": "boolean", "title": "Mark cards done after session", "default": False},
                "dry_run":        {"type": "boolean", "title": "Dry run (no execution)", "default": False},
            },
        }

    def _vault_path(self) -> Path:
        """Local directory where this vault is synced."""
        return _VAULT_BASE / f"ob-{self._plugin_id}"

    async def configure(self, config: dict) -> None:
        self._config = config
        # Attempt login + vault setup on configure
        await asyncio.get_event_loop().run_in_executor(None, self._setup_sync)

    def _setup_sync(self) -> None:
        email = self._config.get("email", "")
        password = self._config.get("password", "")
        vault_name = self._config.get("vault_name", "")

        if not email or not password or not vault_name:
            logger.warning("ObsidianConnector: incomplete config, skipping setup")
            return

        # Login (idempotent — if already logged in with same account, ob reports status)
        result = _ob("login", "--email", email, "--password", password, timeout=30)
        if result.returncode != 0:
            logger.error("ob login failed: %s", result.stderr.strip())
            return
        logger.info("ob login: %s", result.stdout.strip())

        # Set up local sync dir
        vault_path = self._vault_path()
        vault_path.mkdir(parents=True, exist_ok=True)

        result = _ob(
            "sync-setup",
            "--vault", vault_name,
            "--path", str(vault_path),
            "--mode", "pull-only",
            timeout=60,
        )
        if result.returncode != 0:
            # May fail if already set up — that's fine
            logger.warning("ob sync-setup: %s", result.stderr.strip() or result.stdout.strip())
        else:
            logger.info("ob sync-setup OK for vault '%s' at %s", vault_name, vault_path)

        # Initial sync
        result = _ob("sync", "--path", str(vault_path), timeout=120)
        if result.returncode != 0:
            logger.error("ob sync failed: %s", result.stderr.strip())

    async def health(self) -> ConnectorHealth:
        email = self._config.get("email", "")
        vault_name = self._config.get("vault_name", "")
        board_path_rel = self._config.get("board_path", "")

        if not email or not vault_name or not board_path_rel:
            return ConnectorHealth(HealthStatus.ERROR, "Incomplete config: email, vault_name, and board_path are required")

        # Check ob is available
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, lambda: _ob("--version", timeout=5)),
                timeout=10,
            )
            if result.returncode != 0:
                return ConnectorHealth(HealthStatus.ERROR, "obsidian-headless (ob) not available")
        except Exception as exc:
            return ConnectorHealth(HealthStatus.ERROR, f"ob not found: {exc}")

        # Check vault sync status
        vault_path = self._vault_path()
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: _ob("sync-status", "--path", str(vault_path), timeout=10)
                ),
                timeout=15,
            )
            if result.returncode != 0:
                return ConnectorHealth(
                    HealthStatus.DEGRADED,
                    f"Vault not synced yet. Run setup or check credentials. ({result.stderr.strip()})",
                )
        except Exception as exc:
            return ConnectorHealth(HealthStatus.DEGRADED, f"sync-status failed: {exc}")

        # Check board file exists
        board_abs = vault_path / board_path_rel
        if not board_abs.exists():
            return ConnectorHealth(
                HealthStatus.DEGRADED,
                f"Board file not found after sync: {board_path_rel}",
            )

        # Quick parse check
        try:
            result = subprocess.run(
                [sys.executable, str(_MD_KANBAN), "columns", "--file", str(board_abs)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return ConnectorHealth(HealthStatus.DEGRADED, f"Board parse error: {result.stderr.strip()}")
            columns = [c for c in result.stdout.strip().splitlines() if c]
            return ConnectorHealth(
                HealthStatus.HEALTHY,
                f"Vault '{vault_name}' synced — board OK, {len(columns)} column(s): {', '.join(columns)}",
                {"vault_path": str(vault_path), "columns": columns},
            )
        except Exception as exc:
            return ConnectorHealth(HealthStatus.ERROR, str(exc))

    async def sync_now(self) -> bool:
        """Pull latest from Obsidian Sync. Returns True on success."""
        vault_path = self._vault_path()
        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: _ob("sync", "--path", str(vault_path), timeout=60)
                ),
                timeout=90,
            )
            if result.returncode != 0:
                logger.error("ob sync failed: %s", result.stderr.strip())
                return False
            logger.info("ob sync OK: %s", result.stdout.strip())
            return True
        except Exception as exc:
            logger.error("ob sync exception: %s", exc)
            return False

    async def teardown(self) -> None:
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()

    # ── Board access helpers (used by boards router) ───────────────────────────

    def _board_abs(self) -> Path | None:
        rel = self._config.get("board_path", "")
        if not rel:
            return None
        p = self._vault_path() / rel
        return p if p.exists() else None

    def list_columns(self) -> list[str]:
        board = self._board_abs()
        if not board:
            return []
        result = subprocess.run(
            [sys.executable, str(_MD_KANBAN), "columns", "--file", str(board)],
            capture_output=True, text=True, timeout=5,
        )
        return [c for c in result.stdout.strip().splitlines() if c]

    def list_cards(self, column: str, include_done: bool = False) -> list[str]:
        board = self._board_abs()
        if not board:
            return []
        args = [sys.executable, str(_MD_KANBAN), "list", "--file", str(board), "--column", column]
        if include_done:
            args.append("--all")
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
        return [c for c in result.stdout.split("\0") if c.strip()]

