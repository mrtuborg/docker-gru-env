#!/usr/bin/env python3
"""
attributions_db.py — Shared helper for the attributions SQLite database.

The database is the single source of truth for Copilot session cost attribution.
It lives at .gru/attributions.db (relative to repo root) and is
committed to data/attributions.db for CI use.

Schema
------
    attributions (
        session_prefix  TEXT PRIMARY KEY,   -- first 8 chars of session_id
        session_id      TEXT,               -- full UUID (optional)
        issue           INTEGER,            -- GitHub issue number, or -1
        project         INTEGER,            -- GitHub project number
        repo            TEXT,               -- canonical "owner/name"
        source          TEXT,               -- 'manual' | 'auto-branch' | 'auto-commit' | 'repo-default'
        applied_at      TEXT                -- ISO-8601 timestamp (UTC)
    )

Public API
----------
    open_db(path)           → sqlite3.Connection
    upsert(conn, record)    → None
    query_by_prefix(conn, prefix) → dict | None
    query_all(conn)         → list[dict]
    is_attributed(conn, session_id) → bool
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(".gru/attributions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attributions (
    session_prefix  TEXT PRIMARY KEY,
    session_id      TEXT,
    issue           INTEGER,
    project         INTEGER,
    repo            TEXT,
    source          TEXT,
    applied_at      TEXT
);
"""


def open_db(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (or create) the attributions database and return a connection.

    Creates the parent directory and schema if they don't exist yet.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def upsert(
    conn: sqlite3.Connection,
    *,
    session_prefix: str,
    session_id: Optional[str] = None,
    issue: Optional[int] = None,
    project: Optional[int] = None,
    repo: Optional[str] = None,
    source: str,
    applied_at: Optional[str] = None,
) -> None:
    """Insert or replace an attribution record.

    ``session_prefix`` is the primary key (first 8 chars of session_id).
    ``applied_at`` defaults to now (UTC) if omitted.
    """
    if applied_at is None:
        applied_at = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """
        INSERT INTO attributions
            (session_prefix, session_id, issue, project, repo, source, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_prefix) DO UPDATE SET
            session_id = excluded.session_id,
            issue      = excluded.issue,
            project    = excluded.project,
            repo       = excluded.repo,
            source     = excluded.source,
            applied_at = excluded.applied_at
        """,
        (session_prefix, session_id, issue, project, repo, source, applied_at),
    )
    conn.commit()


def query_by_prefix(conn: sqlite3.Connection, prefix: str) -> Optional[dict]:
    """Return the attribution for a given session prefix, or None if not found.

    Accepts any prefix length; matches rows whose session_prefix starts with
    ``prefix`` OR where ``prefix`` starts with session_prefix (8-char key).
    """
    prefix = prefix[:8]  # normalise to stored key length
    row = conn.execute(
        "SELECT * FROM attributions WHERE session_prefix = ?", (prefix,)
    ).fetchone()
    return dict(row) if row else None


def query_all(conn: sqlite3.Connection) -> list[dict]:
    """Return all attribution records as a list of dicts."""
    rows = conn.execute(
        "SELECT * FROM attributions ORDER BY applied_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def is_attributed(conn: sqlite3.Connection, session_id: str) -> bool:
    """Return True if a session (by full or prefix ID) has an attribution record."""
    prefix = session_id[:8]
    row = conn.execute(
        "SELECT 1 FROM attributions WHERE session_prefix = ?", (prefix,)
    ).fetchone()
    return row is not None


def attributed_prefixes(conn: sqlite3.Connection) -> set[str]:
    """Return the set of all session prefixes present in the DB."""
    rows = conn.execute("SELECT session_prefix FROM attributions").fetchall()
    return {r[0] for r in rows}
