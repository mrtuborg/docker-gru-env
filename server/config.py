"""
Config DB — SQLite schema and async access layer.

Tables:
  settings      — key/value server settings
  plugins       — one row per connected plugin instance
  credentials   — encrypted secrets per plugin (see vault.py)
  watcher_runs  — historical watcher run records
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS plugins (
    id          TEXT PRIMARY KEY,
    plugin_type TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS credentials (
    plugin_id  TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      BLOB NOT NULL,
    expires_at TEXT,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (plugin_id, key),
    FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watcher_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id   TEXT NOT NULL,
    board_ref   TEXT NOT NULL,
    started_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    log_path    TEXT,
    FOREIGN KEY (plugin_id) REFERENCES plugins(id)
);
"""


def get_db_path() -> Path:
    if _DB_PATH:
        return _DB_PATH
    data_dir = Path(os.environ.get("GRU_DATA_DIR", Path.home() / ".gru"))
    return data_dir / "server.db"


async def init_db() -> None:
    """Create tables if they don't exist. Called once at startup."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(DDL)
        await db.commit()
    logger.info("Config DB initialized at %s", db_path)


async def get_setting(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value),
        )
        await db.commit()


async def get_all_settings() -> dict[str, str]:
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            return {row[0]: row[1] async for row in cur}


# ── Plugin CRUD ───────────────────────────────────────────────────────────────

async def list_plugins() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM plugins ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_plugin(plugin_id: str) -> dict | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_plugin(plugin_id: str, plugin_type: str, config: dict, enabled: bool = True) -> None:
    config_json = json.dumps(config)
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO plugins(id, plugin_type, config, enabled, updated_at)
               VALUES(?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(id) DO UPDATE SET
                 plugin_type=excluded.plugin_type,
                 config=excluded.config,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (plugin_id, plugin_type, config_json, int(enabled)),
        )
        await db.commit()


async def delete_plugin(plugin_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Watcher run records ───────────────────────────────────────────────────────

async def record_watcher_start(plugin_id: str, board_ref: str, log_path: str | None = None) -> int:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute(
            "INSERT INTO watcher_runs(plugin_id, board_ref, status, log_path) VALUES(?,?,'running',?)",
            (plugin_id, board_ref, log_path),
        )
        await db.commit()
        return cur.lastrowid


async def record_watcher_finish(run_id: int, status: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE watcher_runs SET finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), status=? WHERE id=?",
            (status, run_id),
        )
        await db.commit()


async def list_watcher_runs(plugin_id: str | None = None, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if plugin_id:
            sql = "SELECT * FROM watcher_runs WHERE plugin_id=? ORDER BY started_at DESC LIMIT ?"
            args = (plugin_id, limit)
        else:
            sql = "SELECT * FROM watcher_runs ORDER BY started_at DESC LIMIT ?"
            args = (limit,)
        async with db.execute(sql, args) as cur:
            return [dict(r) async for r in cur]
