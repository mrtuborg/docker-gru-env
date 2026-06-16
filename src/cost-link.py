#!/usr/bin/env python3
"""
cost-link.py — Retroactively attribute unlinked cost sessions to GitHub issues.

Strategies (in order of confidence):
  1. watcher-run context prompt  — first turn contains "issue #N" or "Issue: #N"
  2. session_refs (commit → PR)    — looks up commit SHAs via GitHub API
  3. branch name                   — e.g. fix/42-description, feat/issue-7
  4. text scan                     — issue mentions in turns/checkpoints/summaries
                                     (only when a single issue dominates)

Usage:
    python3 src/cost-link.py [--dry-run] [--gh-host HOST] [--apply]

    Without --apply: shows suggestions (default)
    With --apply:    writes attributions to .gru/attributions.db
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

_COPILOT_HOME    = Path(os.environ.get("COPILOT_DATA_HOME", Path.home() / ".copilot"))
LIVE_JSONL       = _COPILOT_HOME / "cost-log.jsonl"
HISTORICAL_JSONL = _COPILOT_HOME / "cost-log-historical.jsonl"
SESSION_DB       = _COPILOT_HOME / "session-store.db"
DEFAULT_ATTR_DB  = Path(".gru/attributions.db")
MAX_ISSUE        = 500   # ignore numbers above this
MIN_COST_TO_SHOW = 0.01  # skip zero-cost sessions in output


def _load_attr_db_module():
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_repo_aliases(config_path: Optional[str]) -> dict[str, str]:
    """Load repo_aliases from a workflow config YAML. Returns {} if unavailable."""
    if not config_path or not _HAVE_YAML:
        return {}
    try:
        with open(config_path) as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("repo_aliases") or {}
        return {k.lower(): v.lower() for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, Exception):
        return {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh(args: list[str], gh_host: Optional[str] = None) -> Optional[str]:
    env = os.environ.copy()
    if gh_host:
        env["GH_HOST"] = gh_host
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           env=env, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_records() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for path in [HISTORICAL_JSONL, LIVE_JSONL]:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    records[r["session_id"]] = r
                except json.JSONDecodeError:
                    pass
    return records


def open_db() -> Optional[sqlite3.Connection]:
    if not SESSION_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{SESSION_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _extract_issues(text: str) -> list[int]:
    nums = []
    seen: set[int] = set()
    for m in re.finditer(r"#(\d+)", text):
        n = int(m.group(1))
        # Skip single-digit numbers to avoid false positives from patterns
        # like "uname -a ... #1 SMP" or "step #3"
        if n < 10:
            continue
        if n <= MAX_ISSUE and n not in seen:
            seen.add(n)
            nums.append(n)
    return nums

# ---------------------------------------------------------------------------
# Strategy 1: watcher-run context prompt / issue_number sidecar pattern
# Scans ALL turns for:
#   "issue_number: N"  (session-handoff sidecar)
#   "issue #N" / "#N" / "ISSUE_NUM=N"  (watcher-run prompt)
# ---------------------------------------------------------------------------

def _strategy_context_prompt(conn: sqlite3.Connection, sid: str) -> Optional[tuple[int, str]]:
    """Return (issue_num, strategy) scanning all turns for clear issue refs."""
    turns = conn.execute(
        "SELECT user_message FROM turns WHERE session_id=? ORDER BY turn_index",
        (sid,),
    ).fetchall()
    if not turns:
        return None

    # Highest-confidence: explicit "issue_number: N" sidecar in any turn
    for row in turns:
        msg = row["user_message"] or ""
        m = re.search(r"issue_number[:\s]+(\d+)", msg, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= MAX_ISSUE:
                return n, "issue-number-sidecar"

    # High confidence: watcher-run patterns in first turn only
    msg = turns[0]["user_message"] or ""
    for pattern in [
        r"[Ii]ssue[:\s#]+(\d+)",
        r"ISSUE_NUM[=:\s]+(\d+)",
        r"working on #(\d+)",
        r"^#(\d+)\b",
    ]:
        m = re.search(pattern, msg)
        if m:
            n = int(m.group(1))
            if 1 <= n <= MAX_ISSUE:
                return n, "context-prompt"
    return None


# ---------------------------------------------------------------------------
# Strategy 2: commit → PR → issue via GitHub API
# ---------------------------------------------------------------------------

def _strategy_commit_lookup(
    conn: sqlite3.Connection,
    sid: str,
    repo: str,
    gh_host: Optional[str],
) -> Optional[tuple[int, str]]:
    """Return (issue_num, 'commit-pr') if any commit maps to a PR that closes an issue."""
    commits = conn.execute(
        "SELECT ref_value FROM session_refs WHERE session_id=? AND ref_type='commit'",
        (sid,),
    ).fetchall()
    if not commits:
        return None

    for row in commits[:5]:  # check first 5 commits
        sha = row["ref_value"]
        raw = _gh(["api", f"repos/{repo}/commits/{sha}/pulls",
                   "--jq", ".[0].number"], gh_host)
        if not raw or not raw.strip().isdigit():
            continue
        pr_num = int(raw.strip())
        # Get PR body to find "closes #N"
        body = _gh(["api", f"repos/{repo}/pulls/{pr_num}",
                    "--jq", ".body"], gh_host) or ""
        for pattern in [r"[Cc]loses?\s+#(\d+)", r"[Ff]ixes?\s+#(\d+)",
                        r"[Rr]esolves?\s+#(\d+)"]:
            m = re.search(pattern, body)
            if m:
                n = int(m.group(1))
                if 1 <= n <= MAX_ISSUE:
                    return n, f"commit-pr(#{pr_num})"
        # Fall back to PR number itself as proxy
        if 1 <= pr_num <= MAX_ISSUE:
            return pr_num, f"commit-pr(#{pr_num},no-close-ref)"
    return None


# ---------------------------------------------------------------------------
# Strategy 2b: branch → PR lookup (non-generic branches only)
# Queries GitHub for a PR associated with the session's branch, then extracts
# the closing issue from the PR title/body.
# ---------------------------------------------------------------------------

GENERIC_BRANCHES = frozenset({
    "main", "master", "scarthgap", "scarthgap-dev",
    "kirkstone", "kirkstone-dev", "dunfell", "develop",
})


def _strategy_branch_pr(
    repo: str,
    branch: str,
    aliases: Optional[dict[str, str]],
    gh_host: Optional[str],
) -> Optional[tuple[int, str]]:
    """Return (issue_num, 'branch-pr') if this branch has a PR that closes an issue."""
    if not branch or branch in GENERIC_BRANCHES or not gh_host:
        return None
    _aliases = aliases or {}
    canonical = _aliases.get(repo.lower(), repo) if repo else repo
    if not canonical:
        return None
    owner = canonical.split("/")[0]
    raw = _gh(
        ["api", f"repos/{canonical}/pulls",
         "--method", "GET",
         "-f", f"head={owner}:{branch}",
         "-f", "state=all",
         "--jq", ".[0] | {number, title, body}"],
        gh_host,
    )
    if not raw or raw.strip() in ("null", "", "{}"):
        return None
    try:
        pr = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not pr or pr.get("number") is None:
        return None

    pr_num = pr["number"]
    title = pr.get("title") or ""
    body = pr.get("body") or ""

    for text in [body, title]:
        for pattern in [
            r"[Cc]loses?\s+#(\d+)", r"[Ff]ixes?\s+#(\d+)", r"[Rr]esolves?\s+#(\d+)",
            r"\(#(\d+)\)",
        ]:
            m = re.search(pattern, text)
            if m:
                n = int(m.group(1))
                if 10 <= n <= MAX_ISSUE:
                    return n, f"branch-pr(#{pr_num})"
    return None


def _strategy_branch(branch: str) -> Optional[tuple[int, str]]:
    if not branch or branch in ("main", "master", "scarthgap", "kirkstone-dev",
                                 "scarthgap-dev", "kirkstone"):
        return None
    for pattern in [
        r"(?:fix|feat|feature|issue|bug|chore)/issue[-_]?(\d+)",
        r"(?:fix|feat|feature|issue|bug|chore)/(\d+)[-_]",
        r"^(\d+)[-_/]",
        r"[-_/](\d+)$",
    ]:
        m = re.search(pattern, branch, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= MAX_ISSUE:
                return n, f"branch({branch})"
    return None


# ---------------------------------------------------------------------------
# Strategy 4: dominant issue mention across ALL session text
# Accepts when:
#   a) exactly ONE unique issue number appears in the entire session, OR
#   b) one number appears in ≥2 distinct sources and is 2× the second
# ---------------------------------------------------------------------------

def _strategy_text_dominant(conn: sqlite3.Connection, sid: str) -> Optional[tuple[int, str]]:
    texts = []
    sess = conn.execute("SELECT summary FROM sessions WHERE id=?", (sid,)).fetchone()
    if sess:
        texts.append(("summary", sess["summary"] or ""))
    for row in conn.execute(
        "SELECT user_message FROM turns WHERE session_id=? ORDER BY turn_index",
        (sid,),
    ):
        texts.append(("turn", row["user_message"] or ""))
    for row in conn.execute(
        "SELECT title, overview FROM checkpoints WHERE session_id=?", (sid,)
    ):
        texts.append(("checkpoint", (row["title"] or "") + " " + (row["overview"] or "")))

    if not texts:
        return None

    all_text = " ".join(t for _, t in texts)
    all_nums = set(_extract_issues(all_text))

    # Case A: exactly one issue number in the entire session → unambiguous
    if len(all_nums) == 1:
        n = next(iter(all_nums))
        return n, "sole-issue-ref"

    # Case B: one number dominates across multiple sources
    counts: Counter[int] = Counter()
    for _, text in texts:
        for n in set(_extract_issues(text)):
            counts[n] += 1

    if not counts:
        return None
    top = counts.most_common(2)
    if top[0][1] < 2:
        return None
    if len(top) > 1 and top[0][1] < top[1][1] * 2:
        return None
    return top[0][0], f"text-dominant(seen-in-{top[0][1]}-sources)"


# ---------------------------------------------------------------------------
# Main attribution loop
# ---------------------------------------------------------------------------

def attribute_sessions(
    records: dict[str, dict],
    conn: Optional[sqlite3.Connection],
    gh_host: Optional[str],
    verbose: bool = False,
    aliases: Optional[dict[str, str]] = None,
    already_attributed: Optional[set[str]] = None,
) -> list[dict]:
    """
    Return list of patch dicts:
      {session_id, issue, confidence, strategy, repo, cost, current_issue_refs}

    Sessions whose prefix is in ``already_attributed`` (from attributions.db)
    are skipped — they are already handled.
    """
    _attributed = already_attributed or set()
    unlinked = {
        sid: r for sid, r in records.items()
        if not (r.get("issue_refs") or []) and sid[:8] not in _attributed
    }
    print(f"Unlinked sessions: {len(unlinked)}  "
          f"(${sum(r.get('est_cost_usd') or 0 for r in unlinked.values()):.2f})")

    if conn is None:
        print("WARNING: session-store.db not found — text/DB strategies unavailable")
        conn_ok = False
    else:
        conn_ok = True

    patches = []
    skipped_no_signal = 0

    for sid, r in sorted(unlinked.items(),
                          key=lambda x: -(x[1].get("est_cost_usd") or 0)):
        repo   = r.get("repository") or ""
        branch = r.get("branch") or ""
        cost   = r.get("est_cost_usd") or 0

        result = None

        # Strategy 1: context prompt (fast, no API)
        if conn_ok and not result:
            result = _strategy_context_prompt(conn, sid)

        # Strategy 3: branch name heuristic (fast, no API)
        if not result:
            result = _strategy_branch(branch)

        # Strategy 2b: branch → PR lookup (API, non-generic branches)
        if not result and repo and branch and gh_host:
            result = _strategy_branch_pr(repo, branch, aliases, gh_host)

        # Strategy 4: text dominant (no API)
        if conn_ok and not result:
            result = _strategy_text_dominant(conn, sid)

        # Strategy 2: commit→PR (slow, API — try canonical alias first)
        if not result and conn_ok and repo and gh_host:
            _aliases = aliases or {}
            canonical = _aliases.get(repo.lower(), repo)
            result = _strategy_commit_lookup(conn, sid, canonical, gh_host)
            # If alias differs, also try original repo
            if not result and canonical != repo:
                result = _strategy_commit_lookup(conn, sid, repo, gh_host)

        if result:
            issue_num, strategy = result
            patches.append({
                "session_id": sid,
                "issue": issue_num,
                "confidence": "low",
                "strategy": strategy,
                "repo": repo,
                "branch": branch,
                "cost": cost,
            })
        else:
            skipped_no_signal += 1
            if verbose:
                print(f"  no-signal  {sid[:8]}  ${cost:.2f}  {repo}@{branch}")

    total_recovered = sum(p["cost"] for p in patches)
    print(f"\nRecoverable: {len(patches)} sessions  ${total_recovered:.2f}")
    print(f"No signal:   {skipped_no_signal} sessions  "
          f"(${sum((r.get('est_cost_usd') or 0) for r in unlinked.values() if not any(p['session_id'] == s for p in patches for s in [p['session_id']] if s in unlinked)):.2f})")

    return patches


# ---------------------------------------------------------------------------
# Display & apply
# ---------------------------------------------------------------------------

def display_patches(patches: list[dict]) -> None:
    if not patches:
        print("Nothing to patch.")
        return
    print()
    print(f"{'SESSION':9}  {'COST':>8}  {'ISSUE':>6}  {'STRATEGY':<35}  REPO@BRANCH")
    print("-" * 100)
    by_issue: dict[int, float] = defaultdict(float)
    for p in sorted(patches, key=lambda x: -x["cost"]):
        if p["cost"] >= MIN_COST_TO_SHOW:
            print(f"  {p['session_id'][:8]}  ${p['cost']:7.2f}  #{p['issue']:<5}  "
                  f"{p['strategy']:<35}  {p['repo']}@{p['branch']}")
            by_issue[p["issue"]] += p["cost"]
    print()
    print("Cost recovered per issue:")
    for issue, cost in sorted(by_issue.items(), key=lambda x: -x[1]):
        if cost >= MIN_COST_TO_SHOW:
            print(f"  #{issue:<5}  ${cost:.2f}")


def apply_patches(
    patches: list[dict],
    records: dict[str, dict],
    dry_run: bool,
    db_path: Path = DEFAULT_ATTR_DB,
) -> int:
    """Write auto-attributed sessions to attributions.db. Returns count written.

    The JSONL files are never touched — attributions.db is the single source of truth.
    """
    if not patches:
        return 0

    if dry_run:
        print(f"\nDRY-RUN: would write {len(patches)} attribution(s) to {db_path}")
        return len(patches)

    db_mod = _load_attr_db_module()
    conn = db_mod.open_db(db_path)
    written = 0
    for p in patches:
        db_mod.upsert(
            conn,
            session_prefix=p["session_id"][:8],
            session_id=p["session_id"],
            issue=p["issue"],
            project=None,
            repo=p.get("repo") or None,
            source=f"auto-{p.get('confidence', 'low')}",
        )
        written += 1
    conn.close()
    print(f"\nWrote {written} attribution(s) to {db_path}")
    return written


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retroactively attribute unlinked cost sessions to issues.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--gh-host", default=None,
                        help="GitHub Enterprise host (or GH_HOST env var)")
    parser.add_argument("--config", default=None,
                        help="Workflow config YAML for repo_aliases (auto-detected if omitted)")
    parser.add_argument("--apply", action="store_true",
                        help="Write attributions to attributions.db (default: dry-run preview)")
    parser.add_argument("--db", default=str(DEFAULT_ATTR_DB), metavar="PATH",
                        help=f"attributions.db path (default: {DEFAULT_ATTR_DB})")
    parser.add_argument("--skip-api", action="store_true",
                        help="Skip GitHub API commit lookups (faster but less coverage)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    gh_host = args.gh_host or os.environ.get("GH_HOST")

    # Load aliases
    config_path = args.config
    if not config_path:
        for candidate in [".gru/config.yml", ".gru/config.yaml"]:
            if Path(candidate).exists():
                config_path = candidate
                break
    aliases = load_repo_aliases(config_path)
    if aliases:
        print(f"Loaded {len(aliases)} repo aliases from {config_path}")

    records = load_records()
    print(f"Loaded {len(records)} sessions from JSONL logs")

    conn = open_db()
    if conn:
        print(f"Opened session-store.db")

    # Load DB-attributed prefixes so we don't re-process already-attributed sessions
    attr_db_path = Path(getattr(args, "db", None) or DEFAULT_ATTR_DB)
    already_attributed: set[str] = set()
    if attr_db_path.exists():
        try:
            db_mod = _load_attr_db_module()
            attr_conn = db_mod.open_db(attr_db_path)
            already_attributed = db_mod.attributed_prefixes(attr_conn)
            attr_conn.close()
            print(f"Loaded {len(already_attributed)} attributed prefix(es) from {attr_db_path}")
        except Exception as exc:
            print(f"WARNING: could not load {attr_db_path}: {exc}", file=sys.stderr)

    patches = attribute_sessions(
        records, conn,
        gh_host=None if args.skip_api else gh_host,
        verbose=args.verbose,
        aliases=aliases,
        already_attributed=already_attributed,
    )
    display_patches(patches)

    if args.apply:
        apply_patches(patches, records, dry_run=False, db_path=attr_db_path)
    else:
        print("\nRun with --apply to write attributions to the DB.")
        print("Then run build-dashboard.sh to regenerate dashboards.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
