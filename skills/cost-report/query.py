#!/usr/bin/env python3
"""
cost-report skill query engine.

Loads ~/.copilot/cost-log.jsonl (live) and ~/.copilot/cost-log-historical.jsonl
(historical), deduplicates by session_id, inserts into a transient in-memory
SQLite database, and runs one of three query modes:

    python3 query.py                         # default: this week's summary
    python3 query.py --issue N               # per-issue session breakdown
    python3 query.py --top N                 # top-N issues by premium requests
    python3 query.py --since YYYY-MM-DD      # filter: on or after date
    python3 query.py --repo owner/repo       # filter: specific repo

All output is markdown suitable for display in the Copilot chat window.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

LIVE_JSONL = Path.home() / ".copilot" / "cost-log.jsonl"
HISTORICAL_JSONL = Path.home() / ".copilot" / "cost-log-historical.jsonl"
DASH = "—"

log = logging.getLogger("cost-report.query")


# ---------------------------------------------------------------------------
# JSONL loading + dedup
# ---------------------------------------------------------------------------

def load_records(live: Path, historical: Path) -> list[dict]:
    """Load and deduplicate records; live wins on session_id conflict."""
    seen: dict[str, dict] = {}
    for path, label in [(historical, "historical"), (live, "live")]:
        if not path.exists():
            continue
        try:
            with path.open() as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        sid = rec.get("session_id")
                        if sid:
                            seen[sid] = rec
                    except json.JSONDecodeError:
                        log.debug("%s line %d: bad JSON, skipped", path, lineno)
        except OSError as exc:
            log.debug("Cannot read %s: %s", path, exc)
    return list(seen.values())


# ---------------------------------------------------------------------------
# SQLite schema + ingestion
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE sessions (
    session_id           TEXT PRIMARY KEY,
    confidence           TEXT,
    repository           TEXT,
    branch               TEXT,
    started_at           TEXT,
    ended_at             TEXT,
    total_premium_reqs   INTEGER,
    est_cost_usd         REAL
);

CREATE TABLE session_issues (
    session_id           TEXT NOT NULL,
    issue_number         INTEGER NOT NULL,
    confidence           TEXT,
    PRIMARY KEY (session_id, issue_number)
);
"""


def build_db(records: list[dict]) -> sqlite3.Connection:
    """Create an in-memory SQLite database from the deduplicated records."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)

    for rec in records:
        conn.execute(
            "INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?,?,?)",
            (
                rec.get("session_id"),
                rec.get("confidence", "unknown"),
                rec.get("repository"),
                rec.get("branch"),
                rec.get("started_at"),
                rec.get("ended_at"),
                rec.get("total_premium_requests"),
                rec.get("est_cost_usd"),
            ),
        )
        for ref in rec.get("issue_refs") or []:
            issue_num = ref.get("issue")
            if issue_num is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO session_issues VALUES (?,?,?)",
                    (rec.get("session_id"), issue_num, ref.get("confidence", "unknown")),
                )

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _fmt_premium(v) -> str:
    return DASH if v is None else str(int(v))


def _fmt_cost(v) -> str:
    return DASH if v is None else f"${float(v):.4f}"


def _fmt_date(ts: Optional[str]) -> str:
    return (ts or "")[:10] or DASH


def md_table(headers: list[str], rows: list[tuple]) -> str:
    if not rows:
        return ""
    col_w = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        sr = [str(c) if c is not None else DASH for c in row]
        str_rows.append(sr)
        for i, cell in enumerate(sr):
            col_w[i] = max(col_w[i], len(cell))

    def fmt_row(cells):
        return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * w for w in col_w) + " |"
    lines = [fmt_row(headers), sep] + [fmt_row(r) for r in str_rows]
    return "\n".join(lines)


def empty_state(mode: str) -> str:
    msgs = {
        "week":  "No sessions recorded this week.",
        "issue": "No sessions found for that issue.",
        "top":   "No sessions with issue attribution found.",
    }
    return f"_{msgs.get(mode, 'No data found.')}_"


# ---------------------------------------------------------------------------
# Query modes
# ---------------------------------------------------------------------------

def _apply_filters(sql: str, params: list, since: Optional[str], repo: Optional[str]) -> tuple[str, list]:
    """Append WHERE clauses for --since and --repo filters."""
    clauses = []
    if since:
        clauses.append("s.started_at >= ?")
        params.append(since)
    if repo:
        clauses.append("s.repository = ?")
        params.append(repo)
    if clauses:
        joiner = " AND " if "WHERE" in sql.upper() else " WHERE "
        sql += joiner + " AND ".join(clauses)
    return sql, params


def query_week(conn: sqlite3.Connection, since: Optional[str], repo: Optional[str]) -> str:
    """Default mode: this week's sessions grouped by issue."""
    week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    effective_since = since if (since and since > week_start) else week_start

    sql = """
        SELECT
            COALESCE(si.issue_number, -1)      AS issue,
            COUNT(DISTINCT s.session_id)        AS sessions,
            SUM(s.total_premium_reqs)           AS premium_reqs,
            SUM(s.est_cost_usd)                 AS est_cost_usd
        FROM sessions s
        LEFT JOIN session_issues si ON s.session_id = si.session_id
        WHERE s.started_at >= ?
    """
    params: list = [effective_since]
    sql, params = _apply_filters(sql, params, None, repo)
    sql += " GROUP BY COALESCE(si.issue_number, -1) ORDER BY premium_reqs DESC NULLS LAST"

    rows_raw = conn.execute(sql, params).fetchall()
    if not rows_raw:
        return empty_state("week")

    header = f"## Cost Summary — week of {effective_since}\n\n"
    headers = ["Issue", "Sessions", "Premium Req", "Est Cost USD"]
    rows = []
    for r in rows_raw:
        issue_label = "unlinked" if r["issue"] == -1 else f"#{r['issue']}"
        rows.append((issue_label, r["sessions"], _fmt_premium(r["premium_reqs"]), _fmt_cost(r["est_cost_usd"])))
    return header + md_table(headers, rows)


def query_issue(conn: sqlite3.Connection, issue_num: int, since: Optional[str], repo: Optional[str]) -> str:
    """--issue N: session-level breakdown for a specific issue."""
    sql = """
        SELECT
            s.session_id,
            s.started_at,
            s.repository,
            s.branch,
            s.total_premium_reqs,
            s.est_cost_usd,
            s.confidence
        FROM sessions s
        JOIN session_issues si ON s.session_id = si.session_id
        WHERE si.issue_number = ?
    """
    params: list = [issue_num]
    sql, params = _apply_filters(sql, params, since, repo)
    sql += " ORDER BY s.started_at DESC"

    rows_raw = conn.execute(sql, params).fetchall()
    if not rows_raw:
        return empty_state("issue")

    header = f"## Sessions linked to issue #{issue_num}\n\n"
    headers = ["Session", "Date", "Repository", "Branch", "Premium Req", "Est Cost USD", "Confidence"]
    rows = [
        (
            r["session_id"][:8],
            _fmt_date(r["started_at"]),
            r["repository"] or DASH,
            r["branch"] or DASH,
            _fmt_premium(r["total_premium_reqs"]),
            _fmt_cost(r["est_cost_usd"]),
            r["confidence"] or DASH,
        )
        for r in rows_raw
    ]
    totals_reqs = sum(r["total_premium_reqs"] or 0 for r in rows_raw)
    totals_cost = sum(r["est_cost_usd"] or 0.0 for r in rows_raw)
    has_cost = any(r["est_cost_usd"] is not None for r in rows_raw)
    summary = (
        f"\n\n**Total:** {len(rows_raw)} session(s), "
        f"{totals_reqs} premium requests"
        + (f", {_fmt_cost(totals_cost)} estimated cost" if has_cost else "")
    )
    return header + md_table(headers, rows) + summary


def query_top(conn: sqlite3.Connection, top_n: int, since: Optional[str], repo: Optional[str]) -> str:
    """--top N: top-N issues ranked by premium requests."""
    sql = """
        SELECT
            si.issue_number,
            COUNT(DISTINCT s.session_id)  AS sessions,
            SUM(s.total_premium_reqs)     AS premium_reqs,
            SUM(s.est_cost_usd)           AS est_cost_usd
        FROM sessions s
        JOIN session_issues si ON s.session_id = si.session_id
    """
    params: list = []
    sql, params = _apply_filters(sql, params, since, repo)
    sql += f" GROUP BY si.issue_number ORDER BY premium_reqs DESC NULLS LAST LIMIT ?"
    params.append(top_n)

    rows_raw = conn.execute(sql, params).fetchall()
    if not rows_raw:
        return empty_state("top")

    header = f"## Top {top_n} Issues by Premium Requests\n\n"
    headers = ["Issue", "Sessions", "Premium Req", "Est Cost USD"]
    rows = [
        (
            f"#{r['issue_number']}",
            r["sessions"],
            _fmt_premium(r["premium_reqs"]),
            _fmt_cost(r["est_cost_usd"]),
        )
        for r in rows_raw
    ]
    return header + md_table(headers, rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cost-report skill")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--issue", type=int, metavar="N", help="Show sessions for issue N")
    mode.add_argument("--top", type=int, metavar="N", default=None, help="Top N issues by premium requests")
    p.add_argument("--since", metavar="YYYY-MM-DD", help="Filter: on or after this date")
    p.add_argument("--repo", metavar="OWNER/REPO", help="Filter: specific repository")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    records = load_records(LIVE_JSONL, HISTORICAL_JSONL)
    conn = build_db(records)

    if args.issue is not None:
        output = query_issue(conn, args.issue, args.since, args.repo)
    elif args.top is not None:
        output = query_top(conn, args.top, args.since, args.repo)
    else:
        output = query_week(conn, args.since, args.repo)

    conn.close()
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
