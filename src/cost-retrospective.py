#!/usr/bin/env python3
"""
cost-retrospective.py — Backfill historical cost records from Copilot CLI session-state.

Scans ~/.copilot/session-state/*/events.jsonl, recovers exact shutdown metrics when
present, marks unknowns when not, and writes historical JSONL to
~/.copilot/cost-log-historical.jsonl.

Usage:
    python3 cost-retrospective.py [--since DATE] [--repo SLUG] [--dry-run]

Schema version: 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# pricing.py lives alongside this script
sys.path.insert(0, str(Path(__file__).parent))
from pricing import estimate_session_cost  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
_COPILOT_HOME = Path(os.environ.get("COPILOT_DATA_HOME", Path.home() / ".copilot"))
SESSION_STATE_DIR = _COPILOT_HOME / "session-state"
SESSION_STORE_DB = _COPILOT_HOME / "session-store.db"
OUTPUT_JSONL = _COPILOT_HOME / "cost-log-historical.jsonl"

log = logging.getLogger("cost-retrospective")


# ---------------------------------------------------------------------------
# Shared schema dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModelMetrics:
    requests_count: int = 0
    requests_premium: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class IssueRef:
    issue: int
    confidence: str  # "exact" | "low" | "unknown"


@dataclass
class CostRecord:
    schema_version: int
    session_id: str
    confidence: str  # "exact" | "low" | "unknown"
    repository: Optional[str]
    branch: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    issue_refs: list[IssueRef]
    model_metrics: dict[str, ModelMetrics]
    total_premium_requests: Optional[int]
    est_cost_usd: Optional[float]
    code_changes: Optional[dict]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issue_refs"] = [asdict(r) for r in self.issue_refs]
        d["model_metrics"] = {k: asdict(v) for k, v in self.model_metrics.items()}
        return d


# ---------------------------------------------------------------------------
# Parser — reads events.jsonl from a single session directory
# ---------------------------------------------------------------------------

def parse_events(events_path: Path) -> dict:
    """Return dict with keys: start_event, shutdown_event, raw_lines_count."""
    result = {"start_event": None, "shutdown_event": None, "raw_lines_count": 0}
    try:
        with events_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                result["raw_lines_count"] += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("Skipping malformed JSON line in %s", events_path)
                    continue
                etype = event.get("type", "")
                if etype == "session.start" and result["start_event"] is None:
                    result["start_event"] = event
                elif etype == "session.shutdown":
                    result["shutdown_event"] = event  # keep last
    except OSError as exc:
        log.warning("Cannot read %s: %s", events_path, exc)
    return result


# ---------------------------------------------------------------------------
# Normalizer — converts raw event data to CostRecord fields
# ---------------------------------------------------------------------------

def _ms_to_iso(ms: int) -> str:
    """Convert milliseconds epoch to UTC ISO-8601 string."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _relativize_paths(paths: list[str], repo_root: Optional[str]) -> list[str]:
    """Return paths relative to repo_root when possible; fall back to basenames."""
    result = []
    for p in paths:
        if repo_root and p.startswith(repo_root):
            rel = p[len(repo_root):].lstrip("/")
            result.append(rel if rel else p)
        else:
            result.append(os.path.basename(p) or p)
    return result


_MAX_ISSUE_NUMBER = 99_999  # pipeline run IDs and similar can be 6+ digits


def _extract_issue_refs(text: str, confidence: str = "low") -> list[IssueRef]:
    """Extract #N issue references from arbitrary text.

    Numbers above _MAX_ISSUE_NUMBER are skipped — they are almost certainly
    pipeline run IDs or other large identifiers, not GitHub issue numbers.
    """
    refs = []
    seen: set[int] = set()
    for m in re.finditer(r"#(\d+)", text):
        n = int(m.group(1))
        if n > _MAX_ISSUE_NUMBER:
            continue
        if n not in seen:
            seen.add(n)
            refs.append(IssueRef(issue=n, confidence=confidence))
    return refs


def normalize_shutdown(
    session_id: str,
    start_event: Optional[dict],
    shutdown_event: dict,
    workspace_meta: Optional[dict],
    sidecar: Optional[dict] = None,
) -> CostRecord:
    """Build an exact-confidence record from a shutdown event."""
    data = shutdown_event.get("data", {})

    started_at: Optional[str] = None
    if start_event:
        started_at = start_event.get("data", {}).get("startTime")
    if started_at is None and "sessionStartTime" in data:
        started_at = _ms_to_iso(data["sessionStartTime"])

    ended_at = shutdown_event.get("timestamp")

    repository = (workspace_meta or {}).get("repository")
    branch = (workspace_meta or {}).get("branch")
    repo_root = (workspace_meta or {}).get("cwd") or (
        (start_event or {}).get("data", {}).get("context", {}).get("gitRoot")
    )

    raw_files = data.get("codeChanges", {}).get("filesModified", [])
    sanitized_files = _relativize_paths(raw_files, repo_root)
    code_changes = {
        "lines_added": data.get("codeChanges", {}).get("linesAdded"),
        "lines_removed": data.get("codeChanges", {}).get("linesRemoved"),
        "files_modified": sanitized_files,
    }

    model_metrics: dict[str, ModelMetrics] = {}
    total_premium = data.get("totalPremiumRequests")
    for model_name, raw in data.get("modelMetrics", {}).items():
        usage = raw.get("usage", {})
        reqs = raw.get("requests", {})
        model_metrics[model_name] = ModelMetrics(
            requests_count=reqs.get("count", 0),
            requests_premium=reqs.get("cost", 0),
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_read_tokens=usage.get("cacheReadTokens", 0),
            cache_write_tokens=usage.get("cacheWriteTokens", 0),
            reasoning_tokens=usage.get("reasoningTokens", 0),
        )

    # Exact issue refs from issue-refs.json sidecar; fall back to low-confidence text
    if sidecar and sidecar.get("issue_number"):
        issue_refs = [IssueRef(
            issue=sidecar["issue_number"],
            confidence=sidecar.get("confidence", "exact"),
        )]
    else:
        text_for_refs = " ".join(filter(None, [
            (workspace_meta or {}).get("summary", ""),
            branch or "",
            (workspace_meta or {}).get("checkpoint_text", ""),
        ]))
        issue_refs = _extract_issue_refs(text_for_refs, confidence="low")

    return CostRecord(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        confidence="exact",
        repository=repository,
        branch=branch,
        started_at=started_at,
        ended_at=ended_at,
        issue_refs=issue_refs,
        model_metrics=model_metrics,
        total_premium_requests=total_premium,
        est_cost_usd=estimate_session_cost(model_metrics),
        code_changes=code_changes,
    )


def normalize_unknown(
    session_id: str,
    start_event: Optional[dict],
    workspace_meta: Optional[dict],
    sidecar: Optional[dict] = None,
) -> CostRecord:
    """Build an unknown-confidence record (no shutdown event)."""
    started_at: Optional[str] = None
    if start_event:
        started_at = start_event.get("data", {}).get("startTime")

    repository = (workspace_meta or {}).get("repository")
    branch = (workspace_meta or {}).get("branch")

    if sidecar and sidecar.get("issue_number"):
        issue_refs = [IssueRef(
            issue=sidecar["issue_number"],
            confidence=sidecar.get("confidence", "exact"),
        )]
    else:
        text_for_refs = " ".join(filter(None, [
            (workspace_meta or {}).get("summary", ""),
            branch or "",
            (workspace_meta or {}).get("checkpoint_text", ""),
        ]))
        issue_refs = _extract_issue_refs(text_for_refs, confidence="low")

    return CostRecord(
        schema_version=SCHEMA_VERSION,
        session_id=session_id,
        confidence="unknown",
        repository=repository,
        branch=branch,
        started_at=started_at,
        ended_at=None,
        issue_refs=issue_refs,
        model_metrics={},
        total_premium_requests=None,
        est_cost_usd=None,
        code_changes=None,
    )


# ---------------------------------------------------------------------------
# Scanner — discovers and filters sessions
# ---------------------------------------------------------------------------

def _load_workspace_yaml(session_dir: Path) -> Optional[dict]:
    """Load workspace.yaml as a plain dict (YAML parsed manually as key:val)."""
    wp = session_dir / "workspace.yaml"
    if not wp.exists():
        return None
    meta: dict = {}
    try:
        with wp.open() as fh:
            for line in fh:
                line = line.rstrip("\n")
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    except OSError:
        pass
    return meta


def read_sidecar(session_dir: Path) -> Optional[dict]:
    """Read issue-refs.json sidecar written by the issue-start skill."""
    sidecar_path = session_dir / "issue-refs.json"
    if not sidecar_path.exists():
        return None
    try:
        with sidecar_path.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cannot read sidecar %s: %s", sidecar_path, exc)
        return None


def scan_sessions(
    session_state_dir: Path,
    since: Optional[datetime] = None,
    repo_filter: Optional[str] = None,
) -> list[tuple[str, Path, Optional[dict], Optional[dict]]]:
    """
    Return sorted list of (session_id, events_path, workspace_meta, sidecar) tuples,
    filtered by --since and --repo.
    """
    if not session_state_dir.is_dir():
        log.info("session-state directory not found: %s", session_state_dir)
        return []

    results = []
    for entry in sorted(session_state_dir.iterdir()):  # deterministic order
        if not entry.is_dir():
            continue
        session_id = entry.name
        events_path = entry / "events.jsonl"
        if not events_path.exists():
            log.debug("No events.jsonl in %s, skipping", session_id)
            continue

        workspace_meta = _load_workspace_yaml(entry)
        sidecar = read_sidecar(entry)

        # --repo filter
        if repo_filter:
            repo = (workspace_meta or {}).get("repository", "")
            if repo != repo_filter:
                log.debug("Skipping %s: repo=%s (want %s)", session_id, repo, repo_filter)
                continue

        # --since filter (use created_at from workspace.yaml)
        if since:
            created_at_str = (workspace_meta or {}).get("created_at", "")
            try:
                created_at = datetime.fromisoformat(
                    created_at_str.replace("Z", "+00:00")
                )
                if created_at < since:
                    log.debug("Skipping %s: created_at=%s before --since", session_id, created_at_str)
                    continue
            except (ValueError, AttributeError):
                log.debug("Cannot parse created_at=%r for %s, including anyway", created_at_str, session_id)

        results.append((session_id, events_path, workspace_meta, sidecar))

    return results


# ---------------------------------------------------------------------------
# DB join — session_store.db metadata enrichment (used in #82)
# ---------------------------------------------------------------------------

def load_db_metadata(db_path: Path) -> dict[str, dict]:
    """
    Load sessions + checkpoint text from session_store.db, keyed by session_id.
    Returns empty dict if DB is unavailable.
    """
    if not db_path.exists():
        log.debug("session-store.db not found at %s", db_path)
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, repository, branch, summary, created_at, updated_at FROM sessions"
        )
        rows: dict[str, dict] = {row["id"]: dict(row) for row in cur.fetchall()}

        # Aggregate checkpoint titles + overviews per session for issue ref extraction
        cur.execute(
            "SELECT session_id, title, overview FROM checkpoints"
        )
        for cp in cur.fetchall():
            sid = cp["session_id"]
            if sid in rows:
                existing = rows[sid].get("checkpoint_text", "")
                addition = " ".join(filter(None, [cp["title"], cp["overview"]]))
                rows[sid]["checkpoint_text"] = (existing + " " + addition).strip()

        conn.close()
        log.debug("Loaded %d sessions from session-store.db", len(rows))
        return rows
    except sqlite3.Error as exc:
        log.warning("Cannot read session-store.db: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Writer — append records to JSONL
# ---------------------------------------------------------------------------

def write_records(records: list[CostRecord], output_path: Path, dry_run: bool) -> None:
    """Append records to output JSONL; skip duplicates already present."""
    if dry_run:
        return

    existing_ids: set[str] = set()
    if output_path.exists():
        try:
            with output_path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            existing_ids.add(json.loads(line)["session_id"])
                        except (json.JSONDecodeError, KeyError):
                            pass
        except OSError as exc:
            log.warning("Cannot read existing output %s: %s", output_path, exc)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    new_count = 0
    skipped_count = 0
    with output_path.open("a") as fh:
        for rec in records:
            if rec.session_id in existing_ids:
                log.debug("Skipping duplicate session_id=%s", rec.session_id)
                skipped_count += 1
                continue
            fh.write(json.dumps(rec.to_dict(), separators=(",", ":")) + "\n")
            new_count += 1

    log.info(
        "Writer: %d appended, %d skipped (duplicates), output=%s",
        new_count, skipped_count, output_path,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cost-retrospective.py",
        description=(
            "Backfill historical cost records from Copilot CLI session-state directories."
        ),
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Only include sessions created on or after DATE (ISO-8601, e.g. 2025-01-01)",
    )
    parser.add_argument(
        "--repo",
        metavar="SLUG",
        dest="repo",
        help="Only include sessions for repository SLUG (e.g. custom-repo/custom-repo-linux)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print discovered sessions; do not write output JSONL",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=str(OUTPUT_JSONL),
        help=f"Output JSONL path (default: {OUTPUT_JSONL})",
    )
    parser.add_argument(
        "--session-state-dir",
        metavar="DIR",
        default=str(SESSION_STATE_DIR),
        help=f"Session-state directory (default: {SESSION_STATE_DIR})",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=str(SESSION_STORE_DB),
        help=f"session-store.db path (default: {SESSION_STORE_DB})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    since: Optional[datetime] = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            log.error("Invalid --since value %r (expected ISO-8601 date, e.g. 2025-01-01)", args.since)
            return 1

    session_state_dir = Path(args.session_state_dir)
    output_path = Path(args.output)
    db_path = Path(args.db)

    sessions = scan_sessions(
        session_state_dir,
        since=since,
        repo_filter=args.repo,
    )

    if args.dry_run:
        print(f"Dry run — {len(sessions)} session(s) found"
              + (f" (since {args.since})" if args.since else "")
              + (f" (repo={args.repo})" if args.repo else ""))
        for session_id, events_path, meta, _ in sessions:
            created_at = (meta or {}).get("created_at", "unknown")
            repo = (meta or {}).get("repository", "unknown")
            branch = (meta or {}).get("branch", "unknown")
            summary = (meta or {}).get("summary", "")[:60]
            print(f"  {session_id}  {created_at}  {repo}@{branch}  {summary!r}")
        return 0

    # Load DB metadata for enrichment
    db_meta = load_db_metadata(db_path)

    records: list[CostRecord] = []
    stats = {"exact": 0, "unknown": 0, "skipped": 0}

    for session_id, events_path, workspace_meta, sidecar in sessions:
        parsed = parse_events(events_path)

        if parsed["raw_lines_count"] == 0:
            log.debug("Empty events.jsonl for %s, skipping", session_id)
            stats["skipped"] += 1
            continue

        # Enrich workspace_meta with session_store.db if available
        if session_id in db_meta and workspace_meta is not None:
            db_row = db_meta[session_id]
            for key in ("repository", "branch", "summary"):
                if not workspace_meta.get(key) and db_row.get(key):
                    workspace_meta[key] = db_row[key]
            # Include checkpoint text for issue-ref extraction (low confidence)
            if db_row.get("checkpoint_text"):
                workspace_meta["checkpoint_text"] = db_row["checkpoint_text"]

        if parsed["shutdown_event"] is not None:
            rec = normalize_shutdown(
                session_id,
                parsed["start_event"],
                parsed["shutdown_event"],
                workspace_meta,
                sidecar=sidecar,
            )
            stats["exact"] += 1
        else:
            rec = normalize_unknown(
                session_id,
                parsed["start_event"],
                workspace_meta,
                sidecar=sidecar,
            )
            stats["unknown"] += 1

        records.append(rec)

    log.info(
        "Scanned %d session(s): %d exact, %d unknown, %d skipped",
        len(sessions),
        stats["exact"],
        stats["unknown"],
        stats["skipped"],
    )

    write_records(records, output_path, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
