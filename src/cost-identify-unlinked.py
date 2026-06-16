#!/usr/bin/env python3
"""
cost-identify-unlinked.py — Find new unlinked sessions and append them to
manual-attributions.yml for review.

A session is considered "unlinked" if ALL of the following are true:
  1. No issue_refs (or only issue=-1 with no project_hint)
  2. Not already in attributions.db (the single source of truth)
  3. Repo not covered by repo_projects in config (those go to projects automatically)

Usage:
    python3 src/cost-identify-unlinked.py [--min-cost 0.0] [--all]

Options:
    --min-cost FLOAT   Only show sessions above this cost (default: 0.01)
    --all              Include zero-cost sessions too
    --dry-run          Print what would be appended without writing
    --db PATH          attributions.db path (default: .gru/attributions.db)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from collections import defaultdict

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    print("ERROR: PyYAML required. Run: pip install PyYAML", file=sys.stderr)
    sys.exit(1)

LIVE_JSONL       = Path.home() / ".copilot" / "cost-log.jsonl"
HISTORICAL_JSONL = Path.home() / ".copilot" / "cost-log-historical.jsonl"
DEFAULT_CONFIG   = Path(".gru/config.yml")
DEFAULT_MANUAL   = Path(".gru/manual-attributions.yml")
DEFAULT_DB       = Path(".gru/attributions.db")


def _load_attr_db_module():
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def load_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for p in [HISTORICAL_JSONL, LIVE_JSONL]:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
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


def load_manual_prefixes(path: Path) -> set[str]:
    """Return set of session-id prefixes already in the YAML (comments excluded)."""
    if not path.exists():
        return set()
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        return {str(k).strip() for k in raw}
    except Exception:
        return set()


def is_covered_by_config(repo: str, cfg: dict) -> bool:
    """True if the repo is handled automatically via repo_projects or repo_aliases."""
    aliases = {k.lower(): v.lower() for k, v in (cfg.get("repo_aliases") or {}).items()}
    repo_projects = {k.lower(): v for k, v in (cfg.get("repo_projects") or {}).items()}
    repo_l = repo.lower()
    canonical = aliases.get(repo_l, repo_l)
    return (repo_projects.get(repo_l) is not None or
            repo_projects.get(canonical) is not None)


def is_unlinked(r: dict, cfg: dict) -> bool:
    """True if the session should be surfaced for manual attribution."""
    refs = r.get("issue_refs") or []
    # Has real issue refs (positive numbers) → attributed
    if any(ref.get("issue", -1) >= 0 for ref in refs):
        return False
    # Has explicit project hint → attributed
    if r.get("project_hint") is not None:
        return False
    # Repo covered by repo_projects config → attributed automatically
    repo = (r.get("repository") or "").lower()
    if repo and is_covered_by_config(repo, cfg):
        return False
    return True


def format_session_stub(r: dict) -> str:
    """Format a YAML stub for one session, matching the style of manual-attributions.yml."""
    sid = r.get("session_id", "")[:8]
    cost = r.get("est_cost_usd") or 0.0
    repo = r.get("repository") or "(none)"
    branch = r.get("branch") or ""
    repo_at_branch = f"{repo}@{branch}" if branch else repo

    lines = [f"# ${cost:.2f}  {repo_at_branch}"]

    # Add checkpoint titles as context
    checkpoints = r.get("checkpoints") or []
    if checkpoints:
        titles = [c.get("title") or c.get("overview") or "" for c in checkpoints if c]
        titles = [t for t in titles if t][:2]
        if titles:
            lines.append(f"# {' | '.join(titles)}")

    repo_line = f"  repo: {repo}" if repo != "(none)" else "  repo:    # optional override"

    lines += [
        f"{sid}:",
        f"  issue:   # issue number, or -1 if unknown",
        f"  project: # project number",
        repo_line,
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-cost", type=float, default=0.01,
                        help="Minimum cost to include (default: 0.01)")
    parser.add_argument("--all", action="store_true",
                        help="Include zero-cost sessions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print stubs without writing to file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--file", default=str(DEFAULT_MANUAL),
                        metavar="PATH", help="manual-attributions.yml path")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        metavar="PATH", help="attributions.db path")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    records = load_records()
    manual_path = Path(args.file)

    # Load already-attributed prefixes from DB (primary) with YAML as fallback
    db_path = Path(args.db)
    db_prefixes: set[str] = set()
    if db_path.exists():
        db_mod = _load_attr_db_module()
        conn = db_mod.open_db(db_path)
        db_prefixes = db_mod.attributed_prefixes(conn)
        conn.close()
    else:
        # Fallback: use YAML prefixes if DB hasn't been created yet
        db_prefixes = load_manual_prefixes(manual_path)

    min_cost = 0.0 if args.all else args.min_cost

    # Find new unlinked sessions — not in DB and not auto-covered by config
    new_unlinked = []
    for sid, r in records.items():
        if not is_unlinked(r, cfg):
            continue
        cost = r.get("est_cost_usd") or 0.0
        if cost < min_cost:
            continue
        # Check not already attributed in DB
        prefix = sid[:8]
        if prefix in db_prefixes:
            continue
        new_unlinked.append(r)

    # Sort by cost descending
    new_unlinked.sort(key=lambda r: -(r.get("est_cost_usd") or 0.0))

    total_cost = sum(r.get("est_cost_usd") or 0.0 for r in new_unlinked)

    # Report sessions in YAML but with blank values (not yet written to DB)
    pending_in_yaml = []
    if manual_path.exists():
        raw = yaml.safe_load(manual_path.read_text()) or {}
        for k, v in raw.items():
            if isinstance(v, dict) and v.get("issue") is None and v.get("project") is None:
                pending_in_yaml.append(str(k))

    # Summary
    print(f"\n{'═'*55}")
    print(f"  Unlinked session report")
    print(f"{'═'*55}")
    print(f"  Total records loaded:    {len(records)}")
    print(f"  New unlinked sessions:   {len(new_unlinked)}  (${total_cost:.2f})")
    print(f"  Pending in YAML (blank): {len(pending_in_yaml)}")
    print(f"{'═'*55}")

    if not new_unlinked:
        print("\n✅  No new unlinked sessions above threshold.\n")
        if pending_in_yaml:
            print(f"  ⚠  {len(pending_in_yaml)} session(s) in {args.file} still need values.")
            print(f"     Fill them in, then run:\n")
            print(f"     python3 src/cost-link-manual.py --apply")
            print(f"     ./scripts/build-dashboard.sh\n")
        return 0

    print(f"\n  New sessions to review:\n")
    for r in new_unlinked:
        cost = r.get("est_cost_usd") or 0.0
        repo = r.get("repository") or "(none)"
        branch = r.get("branch") or ""
        sid = r.get("session_id", "")[:8]
        checkpoints = r.get("checkpoints") or []
        title = ""
        if checkpoints:
            t = checkpoints[-1].get("title") or checkpoints[-1].get("overview") or ""
            if t:
                title = f"  — {t[:60]}"
        print(f"    {sid}  ${cost:6.2f}  {repo}@{branch}{title}")

    stubs = "\n".join(format_session_stub(r) for r in new_unlinked)

    if args.dry_run:
        print(f"\n{'─'*55}")
        print(f"  DRY RUN — would append to {args.file}:\n")
        print(stubs)
        return 0

    # Append to manual-attributions.yml
    if manual_path.exists():
        existing = manual_path.read_text()
        if not existing.endswith("\n"):
            existing += "\n"
        manual_path.write_text(existing + "\n" + stubs)
    else:
        header = ("# manual-attributions.yml\n"
                  "# Fill in issue: and project: for each session.\n"
                  "# Leave blank to keep unlinked. Use issue: -1 with project: N\n"
                  "# when project is known but issue number is not.\n\n")
        manual_path.write_text(header + stubs)

    print(f"\n  ✅  Appended {len(new_unlinked)} session(s) to {args.file}")
    print(f"\n  Next steps:")
    print(f"    1. Edit {args.file}")
    print(f"       Fill in 'issue:' and 'project:' for each session")
    print(f"       Use issue: -1 if you know the project but not the issue")
    print(f"    2. python3 src/cost-link-manual.py          # preview")
    print(f"    3. python3 src/cost-link-manual.py --apply  # apply")
    print(f"    4. ./scripts/build-dashboard.sh             # rebuild & publish\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
