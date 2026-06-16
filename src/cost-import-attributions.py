#!/usr/bin/env python3
"""
cost-import-attributions.py — One-time (idempotent) migration into attributions.db.

Imports from two sources:
  1. .gru/manual-attributions.yml  → source='manual'
  2. data/cost-log*.jsonl sessions that already have issue_refs patched in
     → source='auto-jsonl'  (these were written by old cost-link*.py tools)

Safe to re-run at any time — existing records are updated in-place (upsert).

Usage:
    python3 src/cost-import-attributions.py [--db PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install PyYAML", file=sys.stderr)
    sys.exit(1)

# Repo-relative paths (script must be run from repo root, or paths adjusted).
DEFAULT_DB      = Path(".gru/attributions.db")
DEFAULT_MANUAL  = Path(".gru/manual-attributions.yml")
DATA_JSONL      = [Path("data/cost-log-historical.jsonl"), Path("data/cost-log.jsonl")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Source 1: manual-attributions.yml
# ---------------------------------------------------------------------------

def load_manual_attributions(path: Path) -> list[dict]:
    """Parse YAML → list of attribution dicts ready for upsert."""
    if not path.exists():
        print(f"  WARN: {path} not found, skipping.", file=sys.stderr)
        return []

    raw = yaml.safe_load(path.read_text()) or {}
    results = []
    for prefix, val in raw.items():
        prefix = str(prefix).strip()
        if not prefix or val is None:
            continue  # explicitly skipped / blank

        if isinstance(val, int):
            results.append({
                "session_prefix": prefix,
                "issue": val,
                "repo": None,
                "project": None,
                "source": "manual",
            })
        elif isinstance(val, dict):
            issue   = val.get("issue")
            repo    = val.get("repo")
            project = val.get("project")
            if issue is None and project is None:
                continue  # placeholder with no values yet
            results.append({
                "session_prefix": prefix,
                "issue":   int(issue)   if issue   is not None else None,
                "project": int(project) if project is not None else None,
                "repo":    repo or None,
                "source":  "manual",
            })
        else:
            try:
                results.append({
                    "session_prefix": prefix,
                    "issue": int(val),
                    "repo": None,
                    "project": None,
                    "source": "manual",
                })
            except (TypeError, ValueError):
                print(f"  WARN: ignoring invalid entry '{prefix}: {val}'", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Source 2: already-patched JSONL sessions (have issue_refs set)
# ---------------------------------------------------------------------------

def load_jsonl_attributions(paths: list[Path]) -> list[dict]:
    """Read JSONL files and extract sessions that already have issue_refs.

    These were patched by the old cost-link*.py tools. We import them into the
    DB so future runs don't need to re-patch JSONL.
    """
    results = []
    seen: set[str] = set()

    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue

            sid = r.get("session_id") or ""
            if not sid:
                continue
            prefix = sid[:8]
            if prefix in seen:
                continue

            refs = r.get("issue_refs") or []
            project_hint = r.get("project_hint")
            repo = r.get("attribution_repo") or r.get("repository") or None

            if refs:
                first = refs[0]
                issue = first.get("issue")
                confidence = first.get("confidence", "auto")
                source = f"auto-{confidence}" if confidence != "manual" else "manual"
                results.append({
                    "session_prefix": prefix,
                    "session_id": sid,
                    "issue": int(issue) if issue is not None else None,
                    "project": int(project_hint) if project_hint is not None else None,
                    "repo": repo,
                    "source": source,
                })
                seen.add(prefix)
            elif project_hint is not None:
                # project_hint but no issue_refs → auto via repo_projects
                results.append({
                    "session_prefix": prefix,
                    "session_id": sid,
                    "issue": -1,
                    "project": int(project_hint),
                    "repo": repo,
                    "source": "repo-default",
                })
                seen.add(prefix)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), metavar="PATH",
                        help=f"attributions.db path (default: {DEFAULT_DB})")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), metavar="PATH",
                        help=f"manual-attributions.yml path (default: {DEFAULT_MANUAL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written without touching the DB")
    args = parser.parse_args()

    # Import the helper from the same src/ directory.
    import importlib.util, os
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Collect records from both sources
    manual_records = load_manual_attributions(Path(args.manual))
    jsonl_records  = load_jsonl_attributions(DATA_JSONL)

    # Merge: manual takes priority over auto-jsonl for the same prefix
    merged: dict[str, dict] = {}
    for r in jsonl_records:
        merged[r["session_prefix"]] = r
    for r in manual_records:
        merged[r["session_prefix"]] = r  # manual wins

    now = _now()
    records = list(merged.values())
    records.sort(key=lambda r: r["session_prefix"])

    print(f"\n{'═'*55}")
    print(f"  cost-import-attributions — migration to attributions.db")
    print(f"{'═'*55}")
    print(f"  From manual YAML:  {len(manual_records)} record(s)")
    print(f"  From JSONL:        {len(jsonl_records)} record(s)")
    print(f"  Merged (unique):   {len(records)} record(s)")
    print(f"{'═'*55}\n")

    if args.dry_run:
        print("  DRY RUN — would write:\n")
        for r in records:
            issue_s   = f"#{r['issue']}" if r.get('issue') is not None else "(none)"
            project_s = f"proj {r['project']}" if r.get('project') is not None else ""
            repo_s    = r.get('repo') or ""
            print(f"    {r['session_prefix']}  {issue_s:>6}  {project_s:<10}  {repo_s}  [{r['source']}]")
        print(f"\n  Would write {len(records)} record(s) to {args.db}")
        return 0

    db_path = Path(args.db)
    conn = mod.open_db(db_path)

    written = 0
    for r in records:
        mod.upsert(
            conn,
            session_prefix=r["session_prefix"],
            session_id=r.get("session_id"),
            issue=r.get("issue"),
            project=r.get("project"),
            repo=r.get("repo"),
            source=r["source"],
            applied_at=now,
        )
        written += 1

    conn.close()

    print(f"  ✅  Wrote {written} record(s) to {args.db}")
    print(f"\n  Next steps:")
    print(f"    python3 src/cost-link-manual.py --apply  # apply any new YAML entries")
    print(f"    python3 src/cost-identify-unlinked.py    # check for remaining gaps")
    print(f"    ./scripts/build-dashboard.sh             # rebuild dashboards\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
