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
import re
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

-- ── Agents ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    source          TEXT NOT NULL DEFAULT 'inline',
    agent_md        TEXT NOT NULL DEFAULT '',
    file_path       TEXT DEFAULT '',
    repo_url        TEXT DEFAULT '',
    repo_path       TEXT DEFAULT '',
    repo_ref        TEXT DEFAULT 'main',
    model           TEXT DEFAULT '',
    tools_json      TEXT DEFAULT '[]',
    mcp_servers_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ── Pipelines ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pipelines (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    enabled               INTEGER DEFAULT 1,
    plugin_id             TEXT NOT NULL,
    board_type            TEXT NOT NULL DEFAULT 'github',
    project_owner         TEXT,
    project_number        INTEGER,
    board_path            TEXT,
    poll_interval         INTEGER DEFAULT 300,
    max_issues            INTEGER DEFAULT 50,
    max_retries           INTEGER DEFAULT 3,
    session_timeout_hours REAL DEFAULT 4.0,
    models_json           TEXT DEFAULT '[]',
    allowed_repos_json    TEXT DEFAULT '[]',
    findings_json         TEXT,
    created_at            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
    pipeline_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'ai',
    agent_id    TEXT DEFAULT '',
    task_prompt TEXT DEFAULT '',
    prompt      TEXT DEFAULT '',
    on_success  TEXT DEFAULT '',
    on_failure  TEXT DEFAULT '',
    on_timeout  TEXT DEFAULT '',
    env_json    TEXT DEFAULT '{}',
    PRIMARY KEY (pipeline_id, column_name),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                TEXT PRIMARY KEY,
    pipeline_id       TEXT NOT NULL,
    started_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ended_at          TEXT,
    status            TEXT DEFAULT 'running',
    issues_processed  INTEGER DEFAULT 0,
    issues_succeeded  INTEGER DEFAULT 0,
    issues_failed     INTEGER DEFAULT 0,
    issues_skipped    INTEGER DEFAULT 0,
    model_used        TEXT,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipeline_run_items (
    run_id        TEXT NOT NULL,
    issue_number  INTEGER NOT NULL,
    issue_repo    TEXT NOT NULL,
    stage         TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT,
    ended_at      TEXT,
    duration_s    REAL,
    model         TEXT,
    cost_usd      REAL,
    session_id    TEXT,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pipeline_state (
    pipeline_id TEXT NOT NULL,
    issue_key   TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (pipeline_id, issue_key),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
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


# ── Agent CRUD ────────────────────────────────────────────────────────────────

async def list_agents() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM agents ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["tools"] = json.loads(d.pop("tools_json", "[]"))
                d["mcp_servers"] = json.loads(d.pop("mcp_servers_json", "{}"))
                result.append(d)
            return result


async def get_agent(agent_id: str) -> dict | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM agents WHERE id=?", (agent_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["tools"] = json.loads(d.pop("tools_json", "[]"))
            d["mcp_servers"] = json.loads(d.pop("mcp_servers_json", "{}"))
            return d


async def upsert_agent(data: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO agents(id, name, description, source, agent_md,
                   file_path, repo_url, repo_path, repo_ref,
                   model, tools_json, mcp_servers_json, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, description=excluded.description,
                   source=excluded.source, agent_md=excluded.agent_md,
                   file_path=excluded.file_path, repo_url=excluded.repo_url,
                   repo_path=excluded.repo_path, repo_ref=excluded.repo_ref,
                   model=excluded.model, tools_json=excluded.tools_json,
                   mcp_servers_json=excluded.mcp_servers_json,
                   updated_at=excluded.updated_at""",
            (
                data["id"], data["name"], data.get("description", ""),
                data.get("source", "inline"), data.get("agent_md", ""),
                data.get("file_path", ""), data.get("repo_url", ""),
                data.get("repo_path", ""), data.get("repo_ref", "main"),
                data.get("model", ""),
                json.dumps(data.get("tools", [])),
                json.dumps(data.get("mcp_servers", {})),
            ),
        )
        await db.commit()


async def delete_agent(agent_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        await db.commit()
        return cur.rowcount > 0


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


def _parse_board_url(url: str) -> tuple[str, int]:
    """Parse a GitHub Projects v2 URL into (owner, number).

    Supports:
      https://HOST/orgs/OWNER/projects/N
      https://HOST/users/OWNER/projects/N
      https://HOST/OWNER/projects/N  (user shorthand)
    Returns ("", 0) if the URL cannot be parsed.
    """
    m = re.search(r"/(?:orgs|users)/([^/]+)/projects/(\d+)", url)
    if not m:
        m = re.search(r"/([^/]+)/projects/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    return "", 0


async def upsert_plugin(plugin_id: str, plugin_type: str, config: dict, enabled: bool = True) -> None:
    # Parse board_url → project_owner + project_number for GitHub plugins
    if plugin_type == "github" and config.get("board_url"):
        owner, number = _parse_board_url(config["board_url"])
        if owner and number:
            config = {**config, "project_owner": owner, "project_number": number}

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


# ── Pipeline CRUD ─────────────────────────────────────────────────────────────

async def list_pipelines() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pipelines ORDER BY created_at") as cur:
            pipelines = [dict(r) for r in await cur.fetchall()]
        for p in pipelines:
            p["models"] = json.loads(p.pop("models_json", "[]"))
            p["allowed_repos"] = json.loads(p.pop("allowed_repos_json", "[]"))
            p["findings"] = json.loads(p["findings_json"]) if p.get("findings_json") else None
            p.pop("findings_json", None)
            async with db.execute(
                "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY stage_index",
                (p["id"],),
            ) as cur2:
                stages = [dict(r) for r in await cur2.fetchall()]
                for s in stages:
                    s["env"] = json.loads(s.pop("env_json", "{}"))
                p["stages"] = stages
        return pipelines


async def get_pipeline(pipeline_id: str) -> dict | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            p = dict(row)
        p["models"] = json.loads(p.pop("models_json", "[]"))
        p["allowed_repos"] = json.loads(p.pop("allowed_repos_json", "[]"))
        p["findings"] = json.loads(p["findings_json"]) if p.get("findings_json") else None
        p.pop("findings_json", None)
        async with db.execute(
            "SELECT * FROM pipeline_stages WHERE pipeline_id=? ORDER BY stage_index",
            (pipeline_id,),
        ) as cur:
            stages = [dict(r) for r in await cur.fetchall()]
            for s in stages:
                s["env"] = json.loads(s.pop("env_json", "{}"))
        p["stages"] = stages
        return p


async def upsert_pipeline(data: dict) -> None:
    pid = data["id"]
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO pipelines(id, name, enabled, plugin_id, board_type,
                   project_owner, project_number, board_path,
                   poll_interval, max_issues, max_retries, session_timeout_hours,
                   models_json, allowed_repos_json, findings_json, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, enabled=excluded.enabled,
                   plugin_id=excluded.plugin_id, board_type=excluded.board_type,
                   project_owner=excluded.project_owner, project_number=excluded.project_number,
                   board_path=excluded.board_path, poll_interval=excluded.poll_interval,
                   max_issues=excluded.max_issues, max_retries=excluded.max_retries,
                   session_timeout_hours=excluded.session_timeout_hours,
                   models_json=excluded.models_json, allowed_repos_json=excluded.allowed_repos_json,
                   findings_json=excluded.findings_json, updated_at=excluded.updated_at""",
            (
                pid, data["name"], int(data.get("enabled", True)),
                data["plugin_id"], data.get("board_type", "github"),
                data.get("project_owner"), data.get("project_number"),
                data.get("board_path"),
                data.get("poll_interval", 300), data.get("max_issues", 50),
                data.get("max_retries", 3), data.get("session_timeout_hours", 4.0),
                json.dumps(data.get("models", [])),
                json.dumps(data.get("allowed_repos", [])),
                json.dumps(data["findings"]) if data.get("findings") else None,
            ),
        )
        # Replace stages
        await db.execute("DELETE FROM pipeline_stages WHERE pipeline_id=?", (pid,))
        for i, stage in enumerate(data.get("stages", [])):
            await db.execute(
                """INSERT INTO pipeline_stages(pipeline_id, stage_index, column_name, actor,
                       agent_id, task_prompt, prompt, on_success, on_failure, on_timeout, env_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid, i, stage["column"], stage.get("actor", "ai"),
                    stage.get("agent_id", ""), stage.get("task_prompt", ""),
                    stage.get("prompt", ""), stage.get("on_success", ""),
                    stage.get("on_failure", ""), stage.get("on_timeout", ""),
                    json.dumps(stage.get("env", {})),
                ),
            )
        await db.commit()


async def delete_pipeline(pipeline_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Pipeline runs ─────────────────────────────────────────────────────────────

async def create_pipeline_run(pipeline_id: str, run_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "INSERT INTO pipeline_runs(id, pipeline_id) VALUES(?,?)",
            (run_id, pipeline_id),
        )
        await db.commit()


async def finish_pipeline_run(run_id: str, status: str, counts: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """UPDATE pipeline_runs SET ended_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                   status=?, issues_processed=?, issues_succeeded=?,
                   issues_failed=?, issues_skipped=?, model_used=?
               WHERE id=?""",
            (
                status, counts.get("processed", 0), counts.get("succeeded", 0),
                counts.get("failed", 0), counts.get("skipped", 0),
                counts.get("model"), run_id,
            ),
        )
        await db.commit()


async def list_pipeline_runs(pipeline_id: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pipeline_runs WHERE pipeline_id=? ORDER BY started_at DESC LIMIT ?",
            (pipeline_id, limit),
        ) as cur:
            return [dict(r) async for r in cur]


async def add_pipeline_run_item(run_id: str, item: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO pipeline_run_items(run_id, issue_number, issue_repo, stage,
                   status, started_at, ended_at, duration_s, model, cost_usd, session_id, error_message)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, item["issue_number"], item["issue_repo"], item["stage"],
                item["status"], item.get("started_at"), item.get("ended_at"),
                item.get("duration_s"), item.get("model"), item.get("cost_usd"),
                item.get("session_id"), item.get("error_message"),
            ),
        )
        await db.commit()


async def get_pipeline_run_items(run_id: str) -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pipeline_run_items WHERE run_id=? ORDER BY started_at",
            (run_id,),
        ) as cur:
            return [dict(r) async for r in cur]


# ── Pipeline state (resume) ──────────────────────────────────────────────────

async def get_pipeline_state(pipeline_id: str) -> dict[str, dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM pipeline_state WHERE pipeline_id=?", (pipeline_id,),
        ) as cur:
            return {r["issue_key"]: dict(r) async for r in cur}


async def set_pipeline_state(pipeline_id: str, issue_key: str, status: str, attempt_count: int) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO pipeline_state(pipeline_id, issue_key, status, attempt_count, updated_at)
               VALUES(?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(pipeline_id, issue_key) DO UPDATE SET
                   status=excluded.status, attempt_count=excluded.attempt_count,
                   updated_at=excluded.updated_at""",
            (pipeline_id, issue_key, status, attempt_count),
        )
        await db.commit()


async def clear_pipeline_state(pipeline_id: str) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM pipeline_state WHERE pipeline_id=?", (pipeline_id,))
        await db.commit()
