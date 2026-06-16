#!/usr/bin/env python3
"""
cost-link-manual.py — Apply hand-written session→issue attributions.

Edit .gru/manual-attributions.yml, then run:

    python3 src/cost-link-manual.py [--apply]

Without --apply: shows what would be written (dry-run).
With --apply:    writes attributions to .gru/attributions.db.

The JSONL files (~/.copilot/cost-log*.jsonl) are NOT modified.
The DB is the single source of truth for attribution.

YAML format (session ID prefix → issue number, optionally with repo/project):

# Real GitHub issue (uses session's repository field)
1899dfe2: 25

# Known project, unknown issue number — use issue: -1 with project number
# Session will appear in that project's dashboard under "#-1 (no issue)"
05c9507d:
  issue: -1
  project: 5
  repo: custom-repo/custom-repo-linux      # optional, overrides session's own repo

# With explicit repo only (overrides session's repository):
298fe3ff:
  issue: 37
  repo: custom-repo/roomboard-linux

# Skip a session (leave it unlinked):
a6d9a454: ~
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    print("ERROR: PyYAML is required. Run: pip install PyYAML", file=sys.stderr)
    sys.exit(1)

# Raw JSONL files are read-only — used only for session metadata lookups.
LIVE_JSONL       = Path.home() / ".copilot" / "cost-log.jsonl"
HISTORICAL_JSONL = Path.home() / ".copilot" / "cost-log-historical.jsonl"
DEFAULT_MANUAL   = Path(".gru/manual-attributions.yml")
DEFAULT_DB       = Path(".gru/attributions.db")

def _load_attr_db_module():
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_manual_attributions(path: Path) -> dict[str, dict]:
    """
    Load attributions from YAML. Returns {session_id_prefix: {issue, repo, project}}.
    Entries with null value are skipped.
    """
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}

    result: dict[str, dict] = {}
    for prefix, val in raw.items():
        prefix = str(prefix).strip()
        if not prefix or val is None:
            continue
        if isinstance(val, int):  # includes negative (e.g. -1 without project hint)
            result[prefix] = {"issue": val, "repo": None, "project": None}
        elif isinstance(val, dict):
            issue = val.get("issue")
            repo  = val.get("repo")
            project = val.get("project")
            if issue is not None:
                result[prefix] = {
                    "issue": int(issue),
                    "repo": repo or None,
                    "project": int(project) if project is not None else None,
                }
        else:
            try:
                result[prefix] = {"issue": int(val), "repo": None, "project": None}
            except (TypeError, ValueError):
                print(f"  WARNING: ignoring invalid entry '{prefix}: {val}'", file=sys.stderr)
    return result


def load_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path, label in [(HISTORICAL_JSONL, "historical"), (LIVE_JSONL, "live")]:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                sid = r.get("session_id")
                if sid:
                    records[sid] = r
            except json.JSONDecodeError:
                pass
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply hand-written session→issue attributions from a YAML file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--file", default=str(DEFAULT_MANUAL), metavar="PATH",
                        help=f"YAML attributions file (default: {DEFAULT_MANUAL})")
    parser.add_argument("--db", default=str(DEFAULT_DB), metavar="PATH",
                        help=f"attributions.db path (default: {DEFAULT_DB})")
    parser.add_argument("--apply", action="store_true",
                        help="Write attributions to DB (default: dry-run)")
    args = parser.parse_args()

    attr_path = Path(args.file)
    if not attr_path.exists():
        print(f"ERROR: attributions file not found: {attr_path}", file=sys.stderr)
        print(f"\nCreate it with the following format:\n")
        print("  # session_id_prefix: issue_number")
        print("  1899dfe2: 25")
        print("  298fe3ff: 45")
        print("  05c9507d:")
        print("    issue: 37")
        print("    repo: custom-repo/roomboard-linux")
        return 1

    manual = load_manual_attributions(attr_path)
    if not manual:
        print("No attributions found in file.")
        return 0

    records = load_records()
    print(f"Loaded {len(records)} sessions, {len(manual)} manual attributions")

    # Load existing DB prefixes so dry-run can distinguish new vs already-saved
    db_mod = _load_attr_db_module()
    db_path = Path(args.db)
    existing_db_prefixes: set[str] = set()
    if db_path.exists():
        conn = db_mod.open_db(db_path)
        existing_db_prefixes = db_mod.attributed_prefixes(conn)
        conn.close()

    # Match prefixes to full session IDs
    patches: list[dict] = []
    for prefix, attr in manual.items():
        matches = [sid for sid in records if sid.startswith(prefix)]
        if not matches:
            print(f"  WARNING: no session found matching prefix '{prefix}'")
            continue
        if len(matches) > 1:
            print(f"  WARNING: prefix '{prefix}' matches {len(matches)} sessions — skipping ambiguous")
            continue

        sid = matches[0]
        r = records[sid]
        cost = r.get("est_cost_usd") or 0
        repo = attr["repo"] or r.get("repository") or ""

        patches.append({
            "session_id": sid,
            "issue": attr["issue"],
            "repo": repo,
            "project": attr.get("project"),
            "cost": cost,
            "in_db": sid[:8] in existing_db_prefixes,
        })
        proj_info = f"  project={attr['project']}" if attr.get("project") else ""
        if not args.apply:
            in_db_tag = "  ✓ in DB" if sid[:8] in existing_db_prefixes else "  ← NEW"
            print(f"  WOULD WRITE  {sid[:8]}  ${cost:.2f}  → #{attr['issue']}  {repo}{proj_info}{in_db_tag}")
        else:
            print(f"  WRITE  {sid[:8]}  ${cost:.2f}  → #{attr['issue']}  {repo}{proj_info}")

    if not patches:
        print("\nNothing to write.")
        return 0

    total = sum(p["cost"] for p in patches)
    print(f"\nTotal: {len(patches)} session(s)  ${total:.2f}")

    if not args.apply:
        print("\nRun with --apply to write to attributions.db.")
        return 0

    # Write to DB (idempotent upserts — JSONL is never touched)
    conn   = db_mod.open_db(Path(args.db))
    written = 0
    for p in patches:
        db_mod.upsert(
            conn,
            session_prefix=p["session_id"][:8],
            session_id=p["session_id"],
            issue=p["issue"],
            project=p.get("project"),
            repo=p["repo"] or None,
            source="manual",
        )
        written += 1
    conn.close()

    print(f"\n✅  Wrote {written} attribution(s) to {args.db}")
    print("Run ./scripts/build-dashboard.sh to regenerate dashboards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
