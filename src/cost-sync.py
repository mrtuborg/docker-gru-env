#!/usr/bin/env python3
"""
cost-sync.py — Real-time cost record sync on sessionEnd.

Called by the Copilot CLI sessionEnd hook (or manually) to read the just-closed
session's events.jsonl, wait for session.shutdown, deduplicate against
~/.copilot/cost-log.jsonl, and append one CostRecord.

Usage:
    python3 cost-sync.py [--session-id ID] [--dry-run]
    COPILOT_SESSION_ID=<id> python3 cost-sync.py          # hook invocation

Manual fallback (equivalent to hook):
    COPILOT_SESSION_ID=<id> python3 scripts/cost-sync.py
    python3 scripts/cost-sync.py --session-id <id> --dry-run

Schema version: 1
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
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
OUTPUT_JSONL = _COPILOT_HOME / "cost-log.jsonl"
MAX_RETRIES = 10
RETRY_DELAY = 1.0  # seconds

log = logging.getLogger("cost-sync")


# ---------------------------------------------------------------------------
# Shared schema dataclasses (intentionally duplicated from cost-retrospective.py
# to keep both scripts independently runnable with no inter-script imports)
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
    project_hint: Optional[int] = None  # project number set by watcher-run for authoritative attribution

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issue_refs"] = [asdict(r) for r in self.issue_refs]
        d["model_metrics"] = {k: asdict(v) for k, v in self.model_metrics.items()}
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _relativize_paths(paths: list[str], repo_root: Optional[str]) -> list[str]:
    result = []
    for p in paths:
        if repo_root and p.startswith(repo_root):
            rel = p[len(repo_root):].lstrip("/")
            result.append(rel if rel else p)
        else:
            result.append(os.path.basename(p) or p)
    return result


def _extract_issue_refs(text: str, confidence: str = "low") -> list[IssueRef]:
    refs: list[IssueRef] = []
    seen: set[int] = set()
    for m in re.finditer(r"#(\d+)", text):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            refs.append(IssueRef(issue=n, confidence=confidence))
    return refs


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_events(events_path: Path) -> tuple[Optional[dict], Optional[dict]]:
    """Return (start_event, shutdown_event) from events.jsonl."""
    start_event: Optional[dict] = None
    shutdown_event: Optional[dict] = None
    try:
        with events_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("Skipping malformed JSON line in %s", events_path)
                    continue
                t = event.get("type", "")
                if t == "session.start" and start_event is None:
                    start_event = event
                elif t == "session.shutdown":
                    shutdown_event = event  # keep last occurrence
    except OSError as exc:
        log.warning("Cannot read %s: %s", events_path, exc)
    return start_event, shutdown_event


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


def read_workspace_meta(session_dir: Path) -> Optional[dict]:
    """Load workspace.yaml as a plain key:value dict (no YAML library needed)."""
    wp = session_dir / "workspace.yaml"
    if not wp.exists():
        return None
    meta: dict = {}
    try:
        with wp.open() as fh:
            for line in fh:
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
    except OSError:
        pass
    return meta


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

def normalize_shutdown(
    session_id: str,
    start_event: Optional[dict],
    shutdown_event: dict,
    workspace_meta: Optional[dict],
    sidecar: Optional[dict],
) -> CostRecord:
    data = shutdown_event.get("data", {})

    # started_at: prefer session.start, then sessionStartTime (ms epoch)
    started_at: Optional[str] = None
    if start_event:
        started_at = start_event.get("data", {}).get("startTime")
    if started_at is None and "sessionStartTime" in data:
        started_at = _ms_to_iso(data["sessionStartTime"])

    ended_at = shutdown_event.get("timestamp")

    # repository / branch: workspace.yaml first, then session.start context
    start_ctx = (start_event or {}).get("data", {}).get("context", {})
    repository = (workspace_meta or {}).get("repository") or start_ctx.get("repository")
    branch = (workspace_meta or {}).get("branch") or start_ctx.get("branch")
    repo_root = (
        (workspace_meta or {}).get("cwd")
        or start_ctx.get("gitRoot")
    )

    code_changes_raw = data.get("codeChanges") or {}
    raw_files = code_changes_raw.get("filesModified", [])
    code_changes = {
        "lines_added": code_changes_raw.get("linesAdded"),
        "lines_removed": code_changes_raw.get("linesRemoved"),
        "files_modified": _relativize_paths(raw_files, repo_root),
    }

    model_metrics: dict[str, ModelMetrics] = {}
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

    # Issue refs: exact sidecar beats low-confidence text extraction
    if sidecar and sidecar.get("issue_number"):
        issue_refs = [IssueRef(
            issue=sidecar["issue_number"],
            confidence=sidecar.get("confidence", "exact"),
        )]
    else:
        text = " ".join(filter(None, [
            (workspace_meta or {}).get("summary", ""),
            branch or "",
        ]))
        issue_refs = _extract_issue_refs(text, confidence="low")

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
        total_premium_requests=data.get("totalPremiumRequests"),
        est_cost_usd=estimate_session_cost(model_metrics),
        code_changes=code_changes,
    )


# ---------------------------------------------------------------------------
# Writer (with flock to prevent TOCTOU duplicates)
# ---------------------------------------------------------------------------

def _session_already_logged_locked(fh, session_id: str) -> bool:
    """Check for session_id in an already-open (and locked) file handle."""
    fh.seek(0)
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            if json.loads(line).get("session_id") == session_id:
                return True
        except json.JSONDecodeError:
            pass
    return False


def append_record_atomic(record: CostRecord, output_path: Path) -> bool:
    """
    Append record to output_path under an exclusive flock.
    Re-checks dedup while locked. Returns True if written, False if deduped.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if _session_already_logged_locked(fh, record.session_id):
                log.info(
                    "session_id=%s already in %s (checked under lock) — deduped",
                    record.session_id, output_path,
                )
                return False
            fh.seek(0, 2)  # seek to end for append
            fh.write(json.dumps(record.to_dict(), separators=(",", ":")) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    log.info("Appended session_id=%s to %s", record.session_id, output_path)
    return True


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def sync_session(
    session_id: str,
    output_path: Path,
    session_state_dir: Path,
    dry_run: bool,
    max_retries: int,
    retry_delay: float,
    repo_override: Optional[str] = None,
    force_repo: Optional[str] = None,
    project_hint: Optional[int] = None,
) -> int:
    session_dir = session_state_dir / session_id
    events_path = session_dir / "events.jsonl"

    if not session_dir.is_dir():
        log.error("Session directory not found: %s", session_dir)
        return 1

    # Retry loop waiting for session.shutdown to appear in events.jsonl
    start_event: Optional[dict] = None
    shutdown_event: Optional[dict] = None
    for attempt in range(max_retries + 1):
        start_event, shutdown_event = read_events(events_path)
        if shutdown_event is not None:
            if attempt > 0:
                log.info("session.shutdown found after %d retry(s)", attempt)
            break
        if attempt < max_retries:
            log.info(
                "Retry %d/%d — session.shutdown not yet in %s; sleeping %.1fs",
                attempt + 1, max_retries, events_path, retry_delay,
            )
            time.sleep(retry_delay)
    else:
        log.warning(
            "session.shutdown not found after %d retries for session_id=%s; skipping",
            max_retries, session_id,
        )
        return 1

    workspace_meta = read_workspace_meta(session_dir)
    sidecar = read_sidecar(session_dir)
    record = normalize_shutdown(
        session_id=session_id,
        start_event=start_event,
        shutdown_event=shutdown_event,
        workspace_meta=workspace_meta,
        sidecar=sidecar,
    )
    if force_repo:
        record.repository = force_repo
    elif repo_override and not record.repository:
        record.repository = repo_override
    if project_hint is not None:
        record.project_hint = project_hint

    if dry_run:
        # Always print the intended record regardless of dedup status
        already = False
        if output_path.exists():
            try:
                with output_path.open() as fh:
                    already = any(
                        json.loads(l.strip()).get("session_id") == session_id
                        for l in fh
                        if l.strip()
                    )
            except OSError:
                pass
        status = "would dedupe (already logged)" if already else "would append"
        print(f"Dry run — {status}:")
        print(json.dumps(record.to_dict(), indent=2))
        return 0

    written = append_record_atomic(record, output_path)
    if written:
        log.info(
            "sync complete: session_id=%s confidence=%s premium_requests=%s",
            session_id, record.confidence, record.total_premium_requests,
        )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cost-sync.py",
        description=(
            "Sync one session's cost record to ~/.copilot/cost-log.jsonl.\n"
            "\n"
            "Called automatically by the Copilot CLI sessionEnd hook, or manually:\n"
            "  python3 scripts/cost-sync.py --session-id <id>\n"
            "  COPILOT_SESSION_ID=<id> python3 scripts/cost-sync.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        help="Session UUID to process (default: COPILOT_SESSION_ID env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended record without writing; shows dedup status",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=MAX_RETRIES,
        metavar="N",
        help=f"Max retry attempts waiting for session.shutdown (default: {MAX_RETRIES})",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=RETRY_DELAY,
        metavar="SECONDS",
        help=f"Seconds between retries (default: {RETRY_DELAY})",
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
        "--repo",
        metavar="OWNER/REPO",
        help="Repository to attribute this session to (e.g. custom-repo/roomboard-linux). "
             "Used as fallback when the session has no repository in its event data "
             "(e.g. when the session ran from a non-git working directory). "
             "Also readable from the GH_REPO environment variable.",
    )
    parser.add_argument(
        "--force-repo",
        metavar="OWNER/REPO",
        help="Force the session repository to this value, overriding whatever the session "
             "workspace detected. Use when watcher sessions run from a working_dir that "
             "differs from the target repo (e.g. platform-workspace vs custom-repo/project).",
    )
    parser.add_argument(
        "--project-hint",
        metavar="N",
        type=int,
        help="Project board number this session belongs to. Stored as project_hint in the "
             "cost record so cost-report can attribute the session to the correct project "
             "by project number, independent of repository name matching.",
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

    session_id = (args.session_id or os.environ.get("COPILOT_SESSION_ID", "")).strip()
    if not session_id:
        log.error(
            "No session ID provided. Use --session-id <id> or set COPILOT_SESSION_ID."
        )
        return 1

    return sync_session(
        session_id=session_id,
        output_path=Path(args.output),
        session_state_dir=Path(args.session_state_dir),
        dry_run=args.dry_run,
        max_retries=args.retries,
        retry_delay=args.retry_delay,
        repo_override=args.repo or os.environ.get("GH_REPO", ""),
        force_repo=args.force_repo or "",
        project_hint=args.project_hint,
    )


if __name__ == "__main__":
    sys.exit(main())
