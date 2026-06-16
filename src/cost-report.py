#!/usr/bin/env python3
"""
cost-report.py — Issue-grouped cost report from merged JSONL logs.

Merges ~/.copilot/cost-log.jsonl (live) and ~/.copilot/cost-log-historical.jsonl
(historical), deduplicates by session_id, and aggregates per-issue totals.

Usage:
    python3 cost-report.py                          # text summary to stdout
    python3 cost-report.py --format html            # write docs/cost-dashboard.html
    python3 cost-report.py --format html --output - # HTML to stdout

Output fields:
    issue          Issue number or "unlinked"
    sessions       Number of sessions attributed to this issue
    premium_reqs   Sum of totalPremiumRequests (— when unknown)
    est_cost_usd   Sum of est_cost_usd (— when unknown)
    confidence     worst-case confidence level across attributed sessions
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COPILOT_HOME = Path(os.environ.get("COPILOT_DATA_HOME", Path.home() / ".copilot"))
LIVE_JSONL = _COPILOT_HOME / "cost-log.jsonl"
HISTORICAL_JSONL = _COPILOT_HOME / "cost-log-historical.jsonl"
DEFAULT_HTML_OUTPUT = Path("docs") / "cost-dashboard.html"
TOP_N = 10

log = logging.getLogger("cost-report")

# ---------------------------------------------------------------------------
# Repo alias helpers — normalize old/renamed repo names to canonical names
# ---------------------------------------------------------------------------

def load_repo_aliases(config_path: Optional[str]) -> dict[str, str]:
    """Load repo_aliases from a workflow config YAML. Returns {} if unavailable."""
    if not config_path:
        return {}
    try:
        import yaml
        with open(config_path) as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("repo_aliases") or {}
        return {k.lower(): v.lower() for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ImportError, Exception):
        return {}


def load_repo_projects(config_path: Optional[str]) -> dict[str, int]:
    """Load repo_projects from config: {repo_lower → project_num}.
    Sessions with that repo and no issue_refs are auto-attributed to that project."""
    if not config_path:
        return {}
    try:
        import yaml
        with open(config_path) as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("repo_projects") or {}
        return {k.lower(): int(v) for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, (int, float))}
    except (OSError, ImportError, Exception):
        return {}


def repo_project_hinted_sessions(
    records: list[dict],
    project_num: int,
    repo_projects: dict[str, int],
    aliases: dict[str, str],
) -> set[str]:
    """
    Return session IDs for sessions whose repo maps to project_num in repo_projects.
    Includes sessions regardless of whether they have issue_refs — if their refs
    don't match any board issue, aggregate_by_issue will use this as a fallback
    and attribute them as '#-1 (no issue)'.
    """
    result = set()
    for r in records:
        if r.get("project_hint") is not None:
            continue  # already explicitly hinted, don't override
        repo = (r.get("repository") or "").lower()
        canonical = aliases.get(repo, repo)
        if repo_projects.get(repo) == project_num or repo_projects.get(canonical) == project_num:
            sid = r.get("session_id")
            if sid:
                result.add(sid)
    return result


def expand_allowed_with_aliases(
    allowed: set[tuple[str, int]],
    aliases: dict[str, str],
) -> set[tuple[str, int]]:
    """
    Expand an allowed_issues set to include alias variants.

    If aliases = {"<owner>/custom-repo-linux": "custom-repo/custom-repo-linux"}
    and allowed has ("custom-repo/custom-repo-linux", 66), also add
    ("<owner>/custom-repo-linux", 66) so sessions on the old repo name match.
    """
    if not aliases:
        return allowed
    # Build reverse map: canonical_lower → [alias1, alias2, ...]
    reverse: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        reverse.setdefault(canonical, []).append(alias)

    expanded = set(allowed)
    for (repo, num) in allowed:
        for alias in reverse.get(repo.lower(), []):
            expanded.add((alias, num))
    return expanded

# ---------------------------------------------------------------------------
# JSONL reader — merges two files, deduplicates by session_id
# ---------------------------------------------------------------------------

def load_records(live_path: Path, historical_path: Path) -> list[dict]:
    """
    Read both JSONL files, merge, and deduplicate by session_id.
    Live records take precedence over historical for the same session_id.
    Returns list sorted by started_at ascending (nulls last).
    """
    records: dict[str, dict] = {}  # session_id → record

    for path, label in [(historical_path, "historical"), (live_path, "live")]:
        if not path.exists():
            log.debug("%s JSONL not found: %s", label, path)
            continue
        count = 0
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
                            records[sid] = rec  # live overwrites historical
                            count += 1
                    except json.JSONDecodeError:
                        log.warning("%s line %d: malformed JSON, skipped", path, lineno)
        except OSError as exc:
            log.warning("Cannot read %s: %s", path, exc)
        log.debug("Loaded %d records from %s", count, path)

    log.info(
        "Merged: %d unique session(s) from live=%s historical=%s",
        len(records),
        "present" if live_path.exists() else "absent",
        "present" if historical_path.exists() else "absent",
    )

    def _sort_key(r: dict) -> tuple:
        ts = r.get("started_at") or ""
        return (0 if ts else 1, ts)

    return sorted(records.values(), key=_sort_key)


# ---------------------------------------------------------------------------
# DB attribution overlay — apply attributions.db at render time
# ---------------------------------------------------------------------------

def apply_db_attributions(records: list[dict], db_path: Path) -> list[dict]:
    """Overlay attribution data from attributions.db onto JSONL records (in-place copies).

    For each record whose session_id prefix is in the DB, synthesise issue_refs,
    project_hint, and attribution_repo from the DB row — exactly as the old
    cost-link*.py tools did by patching JSONL, but without touching any file.

    Records with existing issue_refs are NOT overwritten unless the DB entry has
    source='manual' (manual attribution wins over auto-detected JSONL fields).
    """
    if not db_path.exists():
        log.debug("attributions.db not found at %s — skipping DB overlay", db_path)
        return records

    try:
        import importlib.util
        here = Path(__file__).parent
        spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        conn = mod.open_db(db_path)
        db_attrs = {r["session_prefix"]: r for r in mod.query_all(conn)}
        conn.close()
        log.debug("Loaded %d attribution(s) from %s", len(db_attrs), db_path)
    except Exception as exc:
        log.warning("Could not load attributions.db: %s", exc)
        return records

    result = []
    enriched = 0
    for rec in records:
        sid = rec.get("session_id") or ""
        prefix = sid[:8]
        attr = db_attrs.get(prefix)
        if attr is None:
            result.append(rec)
            continue

        existing_refs = rec.get("issue_refs") or []
        is_manual = attr.get("source") == "manual"
        # Apply DB attribution if: no existing refs, OR this is a manual override
        if not existing_refs or is_manual:
            rec = dict(rec)  # shallow copy — never mutate the original
            issue   = attr.get("issue")
            project = attr.get("project")
            repo    = attr.get("repo")

            if issue is not None:
                confidence = "exact" if is_manual else "low"
                rec["issue_refs"] = [{"issue": issue, "confidence": confidence}]
            if project is not None:
                rec["project_hint"] = project
            if repo:
                rec["attribution_repo"] = repo
            enriched += 1

        result.append(rec)

    log.info("DB overlay applied: %d/%d record(s) enriched from DB", enriched, len(records))
    return result


# ---------------------------------------------------------------------------
# Aggregation — group records by issue number
# ---------------------------------------------------------------------------

CONFIDENCE_RANK = {"exact": 0, "low": 1, "unknown": 2}


def _worst_confidence(a: str, b: str) -> str:
    ra = CONFIDENCE_RANK.get(a, 2)
    rb = CONFIDENCE_RANK.get(b, 2)
    return a if ra >= rb else b


# ---------------------------------------------------------------------------
# GitHub project fetching
# ---------------------------------------------------------------------------

def _gh(args: list[str], gh_host: Optional[str] = None) -> Optional[str]:
    """Run a gh CLI command and return stdout, or None on failure."""
    env = os.environ.copy()
    if gh_host:
        env["GH_HOST"] = gh_host
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, env=env, timeout=30,
        )
        if result.returncode != 0:
            log.debug("gh %s failed: %s", " ".join(args), result.stderr.strip())
            return None
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("gh CLI error: %s", exc)
        return None


def _run_gh_graphql(query: str, variables: dict, gh_host: Optional[str]) -> Optional[dict]:
    """Run a GraphQL query and return the parsed JSON data, or None on failure."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-F" if isinstance(v, int) else "-f", f"{k}={v}"]
    raw = _gh(args, gh_host=gh_host)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def fetch_project_issues(repo: str, project_num: int, gh_host: Optional[str]) -> Optional[set[tuple[str, int]]]:
    """
    Return the set of (repo_nameWithOwner, issue_number) pairs in a GitHub project,
    or None on failure.  Storing the repo prevents cross-project number collisions
    when multiple projects contain issues from different repositories.
    Tries organization first, then user (for personal accounts).
    """
    owner = repo.split("/")[0]

    for entity in ("organization", "user"):
        query = f"""
        query($login: String!, $num: Int!) {{
          {entity}(login: $login) {{
            projectV2(number: $num) {{
              title
              items(first: 100) {{
                nodes {{
                  content {{
                    ... on Issue {{ number state repository {{ nameWithOwner }} }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        data = _run_gh_graphql(query, {"login": owner, "num": project_num}, gh_host)
        if data is None:
            continue
        try:
            proj = data["data"][entity]["projectV2"]
        except (KeyError, TypeError):
            continue
        if proj is None:
            continue
        title = proj.get("title", f"Project {project_num}")
        issues: set[tuple[str, int]] = set()
        for node in proj["items"]["nodes"]:
            c = node.get("content")
            if c and c.get("number") and c.get("repository"):
                issues.add((c["repository"]["nameWithOwner"], c["number"]))
        log.info("Project #%d '%s': %d issue(s)", project_num, title, len(issues))
        return issues

    log.warning("Could not fetch project #%d for %s (tried org and user)", project_num, owner)
    return None


def fetch_all_projects(repo: str, gh_host: Optional[str]) -> list[dict]:
    """
    Return list of {number, title} for all projects linked to the repo's owner.
    Tries organization first, then user.
    """
    owner = repo.split("/")[0]

    for entity in ("organization", "user"):
        query = f"""
        query($login: String!) {{
          {entity}(login: $login) {{
            projectsV2(first: 50) {{
              nodes {{ number title }}
            }}
          }}
        }}
        """
        data = _run_gh_graphql(query, {"login": owner}, gh_host)
        if data is None:
            continue
        try:
            return data["data"][entity]["projectsV2"]["nodes"]
        except (KeyError, TypeError):
            continue
    return []


# ---------------------------------------------------------------------------
# Model / token helpers
# ---------------------------------------------------------------------------

def _short_model_name(model_id: str) -> str:
    """Return a compact display label: 'claude-opus-4.6' → 'Opus 4.6', 'gpt-5.4' → 'GPT-5.4'."""
    m = model_id.lower()
    if m.startswith("gpt-"):
        rest = m[4:]  # "5.4", "5.3-codex"
        parts = rest.split("-")
        name = "GPT-" + parts[0]
        if len(parts) > 1 and parts[1] != "mini":
            name += " " + parts[1].capitalize()
        elif "mini" in rest:
            name += " Mini"
        return name
    if m.startswith("claude-"):
        m = m[7:]
    parts = m.split("-")
    # parts: ["opus","4.6"] or ["sonnet","4.5"] or ["haiku","4.5"]
    if len(parts) >= 2:
        return f"{parts[0].capitalize()} {parts[1]}"
    return model_id


def _summarize_record_tokens(rec: dict) -> dict:
    """Return token summary for one session record."""
    mm = rec.get("model_metrics") or {}
    if not mm:
        return {
            "has_metrics": False, "total_tokens": 0,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "top_model": None, "top_model_requests": 0, "total_requests": 0,
        }
    total_in    = sum(v.get("input_tokens", 0) or 0 for v in mm.values())
    total_out   = sum(v.get("output_tokens", 0) or 0 for v in mm.values())
    total_cache = sum(v.get("cache_read_tokens", 0) or 0 for v in mm.values())
    total_reqs  = sum(v.get("requests_count", 0) or 0 for v in mm.values())
    top_model, top_stats = max(mm.items(), key=lambda x: (x[1].get("requests_count") or 0, x[0]))
    return {
        "has_metrics": True,
        "total_tokens": total_in + total_out,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cache_read_tokens": total_cache,
        "top_model": top_model,
        "top_model_requests": top_stats.get("requests_count") or 0,
        "total_requests": total_reqs,
    }


def aggregate_by_model(records: list[dict]) -> list[dict]:
    """
    Aggregate model_metrics across all records.
    Returns list sorted by requests_count descending:
      { model_id, display_name, sessions, requests_count, requests_premium,
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
        reasoning_tokens, total_tokens }
    """
    from collections import defaultdict
    totals: dict[str, dict] = defaultdict(lambda: dict(
        sessions=0,
        requests_count=0, requests_premium=0,
        input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
    ))
    for rec in records:
        mm = rec.get("model_metrics") or {}
        for model, stats in mm.items():
            t = totals[model]
            t["sessions"] += 1
            for k in ("requests_count", "requests_premium", "input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
                t[k] += stats.get(k) or 0
    return [
        {
            "model_id": mid,
            "display_name": _short_model_name(mid),
            "total_tokens": t["input_tokens"] + t["output_tokens"],
            **t,
        }
        for mid, t in sorted(totals.items(), key=lambda x: -x[1]["requests_count"])
    ]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_by_issue(
    records: list[dict],
    allowed_issues: Optional[set[tuple[str, int]]] = None,
    hinted_sessions: Optional[set[str]] = None,
    project_num: Optional[int] = None,
) -> dict[str, dict]:
    """
    Return dict keyed by issue label (e.g. '#86' or 'unlinked').

    When allowed_issues is set (project-filtered mode):
    - allowed_issues is a set of (repo_nameWithOwner, issue_number) pairs.
    - Sessions whose (repository, issue_num) intersect the allowed set → included.
    - Sessions with project_hint == project_num → included using their issue_refs
      directly, bypassing repo matching (authoritative attribution from watcher-run).
    - Sessions with refs that don't match either path → excluded entirely.
    - Sessions with NO refs → skipped (live in the Unlinked virtual project).

    hinted_sessions: set of session_ids explicitly assigned to this project via
    project_hint (issue=-1, project known). They are included as "#-1 (no issue)".
    """
    groups: dict[str, dict] = defaultdict(lambda: {
        "sessions": 0,
        "premium_reqs": 0,
        "est_cost_usd": 0.0,
        "confidence": "exact",
        "premium_unknown": False,
        "cost_partial": False,
        "cost_sessions": 0,
        "model_reqs": defaultdict(int),  # model_id → total requests_count
    })

    for rec in records:
        issue_refs = rec.get("issue_refs") or []
        ref_nums = [r["issue"] for r in issue_refs]
        rec_repo = rec.get("repository") or ""

        if allowed_issues is not None:
            matching = [n for n in ref_nums if (rec_repo, n) in allowed_issues]
            if not matching:
                rec_project = rec.get("project_hint")
                if project_num is not None and rec_project == project_num:
                    # Explicit project_hint: trust issue_refs directly
                    matching = ref_nums if ref_nums else [-1]
                elif hinted_sessions and rec.get("session_id") in hinted_sessions:
                    # repo_projects fallback: repo maps to this project but refs
                    # don't match any board issue → attribute as "no issue"
                    matching = [-1]
            if not matching and ref_nums:
                continue  # belongs to a different project
            if not matching:
                continue  # unlinked — lives in the Unlinked virtual project
            keys = [f"#{n}" if n >= 0 else "#-1 (no issue)" for n in matching]
        else:
            keys = [f"#{r['issue']}" for r in issue_refs] if issue_refs else ["unlinked"]

        conf = rec.get("confidence", "unknown")
        premium = rec.get("total_premium_requests")
        cost = rec.get("est_cost_usd")

        for key in keys:
            g = groups[key]
            g["sessions"] += 1
            g["confidence"] = _worst_confidence(g["confidence"], conf)
            if premium is None:
                g["premium_unknown"] = True
            else:
                g["premium_reqs"] += premium
            if cost is None:
                g["cost_partial"] = True
            else:
                g["est_cost_usd"] += cost
                g["cost_sessions"] += 1
            for model, stats in (rec.get("model_metrics") or {}).items():
                g["model_reqs"][model] += stats.get("requests_count") or 0

    result = {}
    for key, g in groups.items():
        cost_val = g["est_cost_usd"] if g["cost_sessions"] > 0 else None
        # Compute top model + share
        mr = g["model_reqs"]
        if mr:
            total_r = sum(mr.values())
            top_models = [
                {"name": _short_model_name(mid), "share": int(mr[mid] / total_r * 100) if total_r else 0}
                for mid in sorted(mr, key=lambda m: -mr[m])[:3]
                if mr[mid] > 0
            ]
        else:
            top_models = []
        result[key] = {
            "sessions": g["sessions"],
            "premium_reqs": None if g["premium_unknown"] else g["premium_reqs"],
            "est_cost_usd": cost_val,
            "cost_partial": g["cost_partial"] and cost_val is not None,
            "confidence": g["confidence"],
            "top_models": top_models,
            # keep top_model for backward-compat (pie charts, index page)
            "top_model": top_models[0]["name"] if top_models else None,
        }

    return result


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_DASH = "—"


def _fmt_premium(v: Optional[int]) -> str:
    return _DASH if v is None else str(v)


def _fmt_cost(v: Optional[float], partial: bool = False) -> str:
    if v is None:
        return _DASH
    prefix = "~" if partial else ""
    return f"{prefix}${v:.4f}"


def _unlinked_warning_text(u: dict) -> str:
    cost_str = _fmt_cost(u["est_cost_usd"], u.get("cost_partial", False))
    dr = f"  ({u['date_range']})" if u.get("date_range") else ""
    return (
        f"⚠  {u['sessions']} unlinked session(s) excluded from this project view "
        f"(est. cost: {cost_str}){dr}"
    )


def render_text(records: list[dict], aggregated: dict[str, dict], title: str = "") -> str:
    lines = []
    heading = f"Cost Report — {len(records)} session(s) loaded"
    if title:
        heading += f"  [{title}]"
    lines.append(heading)
    lines.append("")

    aggregated.pop("_unlinked_summary", None)  # no longer shown; sessions live in the Unlinked project

    # Sort by premium_reqs descending (None last)
    def sort_key(item):
        pr = item[1]["premium_reqs"]
        return (0 if pr is None else 1, -(pr or 0))

    sorted_issues = sorted(aggregated.items(), key=sort_key)

    col_w = [max(len(k), 12) for k in ["Issue", "Sessions", "Premium Req", "Est Cost USD", "Confidence"]]
    for key, g in sorted_issues:
        col_w[0] = max(col_w[0], len(key))
        col_w[1] = max(col_w[1], len(str(g["sessions"])))
        col_w[2] = max(col_w[2], len(_fmt_premium(g["premium_reqs"])))
        col_w[3] = max(col_w[3], len(_fmt_cost(g["est_cost_usd"], g.get("cost_partial", False))))
        col_w[4] = max(col_w[4], len(g["confidence"]))

    header = (
        f"{'Issue':<{col_w[0]}}  "
        f"{'Sessions':>{col_w[1]}}  "
        f"{'Premium Req':>{col_w[2]}}  "
        f"{'Est Cost USD':>{col_w[3]}}  "
        f"{'Confidence':<{col_w[4]}}"
    )
    sep = "  ".join("-" * w for w in col_w)
    lines.append(header)
    lines.append(sep)

    for key, g in sorted_issues:
        lines.append(
            f"{key:<{col_w[0]}}  "
            f"{g['sessions']:>{col_w[1]}}  "
            f"{_fmt_premium(g['premium_reqs']):>{col_w[2]}}  "
            f"{_fmt_cost(g['est_cost_usd'], g.get('cost_partial', False)):>{col_w[3]}}  "
            f"{g['confidence']:<{col_w[4]}}"
        )

    lines.append("")
    lines.append("By repository/branch:")
    rb_rows = aggregate_by_repo_branch(records)
    rb_col = [max(len("Repository@Branch"), max((len(r["repo_branch"]) for r in rb_rows), default=0)),
              len("Sessions"), len("Premium Req"), len("Est Cost USD")]
    rb_header = (
        f"{'Repository@Branch':<{rb_col[0]}}  "
        f"{'Sessions':>{rb_col[1]}}  "
        f"{'Premium Req':>{rb_col[2]}}  "
        f"{'Est Cost USD':>{rb_col[3]}}"
    )
    lines.append(rb_header)
    lines.append("  ".join("-" * w for w in rb_col))
    for r in rb_rows:
        lines.append(
            f"{r['repo_branch']:<{rb_col[0]}}  "
            f"{r['sessions']:>{rb_col[1]}}  "
            f"{_fmt_premium(r['premium_reqs']):>{rb_col[2]}}  "
            f"{_fmt_cost(r['est_cost_usd'], r['cost_partial']):>{rb_col[3]}}"
        )

    lines.append("")
    lines.append("Session timeline (most recent first):")
    for rec in reversed(records[:50]):
        sid_short = rec.get("session_id", "?")[:8]
        started = (rec.get("started_at") or "unknown")[:10]
        repo = rec.get("repository") or "?"
        branch = rec.get("branch") or "?"
        premium = _fmt_premium(rec.get("total_premium_requests"))
        conf = rec.get("confidence", "?")
        refs = ", ".join(f"#{r['issue']}" for r in (rec.get("issue_refs") or []))
        lines.append(
            f"  {sid_short}  {started}  {repo}@{branch}  "
            f"reqs={premium}  conf={conf}  issues={refs or '—'}"
        )

    return "\n".join(lines)


def aggregate_by_period(records: list[dict], period: str = "month") -> list[dict]:
    """
    Group records by calendar period ('week', 'month', or 'year').
    Returns list of dicts sorted by period ascending:
      { period, sessions, premium_reqs, est_cost_usd, cost_partial,
        total_tokens, input_tokens, output_tokens, cache_read_tokens, token_sessions }
    """
    from collections import defaultdict
    buckets: dict[str, dict] = defaultdict(lambda: {
        "sessions": 0,
        "premium_reqs": 0,
        "premium_unknown": False,
        "est_cost_usd": 0.0,
        "cost_sessions": 0,
        "cost_partial": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "token_sessions": 0,
    })

    for rec in records:
        date_str = (rec.get("started_at") or "")[:10]
        if not date_str:
            key = "unknown"
        elif period == "week":
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            except ValueError:
                key = "unknown"
        elif period == "year":
            key = date_str[:4]
        else:  # month (default)
            key = date_str[:7]

        b = buckets[key]
        b["sessions"] += 1
        premium = rec.get("total_premium_requests")
        if premium is None:
            b["premium_unknown"] = True
        else:
            b["premium_reqs"] += premium
        cost = rec.get("est_cost_usd")
        if cost is None:
            b["cost_partial"] = True
        else:
            b["est_cost_usd"] += cost
            b["cost_sessions"] += 1
        # Token aggregation from model_metrics
        tok = _summarize_record_tokens(rec)
        if tok["has_metrics"]:
            b["input_tokens"]      += tok["input_tokens"]
            b["output_tokens"]     += tok["output_tokens"]
            b["cache_read_tokens"] += tok["cache_read_tokens"]
            b["token_sessions"]    += 1

    rows = []
    for key in sorted(buckets):
        b = buckets[key]
        rows.append({
            "period":            key,
            "sessions":          b["sessions"],
            "premium_reqs":      None if b["premium_unknown"] else b["premium_reqs"],
            "est_cost_usd":      b["est_cost_usd"] if b["cost_sessions"] > 0 else None,
            "cost_partial":      b["cost_partial"] and b["cost_sessions"] > 0,
            "total_tokens":      b["input_tokens"] + b["output_tokens"],
            "input_tokens":      b["input_tokens"],
            "output_tokens":     b["output_tokens"],
            "cache_read_tokens": b["cache_read_tokens"],
            "token_sessions":    b["token_sessions"],
        })
    return rows


def aggregate_by_repo_branch(records: list[dict]) -> list[dict]:
    """
    Group records by repository and branch.
    Returns list sorted by sessions descending:
      { repo_branch, repository, branch, sessions, premium_reqs, est_cost_usd,
        cost_partial, top_model, top_model_share }
    """
    from collections import defaultdict
    buckets: dict[tuple, dict] = {}

    for rec in records:
        repo = rec.get("repository") or "?"
        branch = rec.get("branch") or "?"
        key = (repo, branch)
        if key not in buckets:
            buckets[key] = {
                "sessions": 0,
                "premium_reqs": 0,
                "premium_unknown": False,
                "est_cost_usd": 0.0,
                "cost_sessions": 0,
                "cost_partial": False,
                "model_reqs": defaultdict(int),
            }
        b = buckets[key]
        b["sessions"] += 1
        premium = rec.get("total_premium_requests")
        if premium is None:
            b["premium_unknown"] = True
        else:
            b["premium_reqs"] += premium
        cost = rec.get("est_cost_usd")
        if cost is None:
            b["cost_partial"] = True
        else:
            b["est_cost_usd"] += cost
            b["cost_sessions"] += 1
        for model, stats in (rec.get("model_metrics") or {}).items():
            b["model_reqs"][model] += stats.get("requests_count") or 0

    rows = []
    for (repo, branch), b in sorted(buckets.items(), key=lambda x: -x[1]["sessions"]):
        mr = b["model_reqs"]
        if mr:
            total_r = sum(mr.values())
            top_models = [
                {"name": _short_model_name(mid), "share": int(mr[mid] / total_r * 100) if total_r else 0}
                for mid in sorted(mr, key=lambda m: -mr[m])[:3]
                if mr[mid] > 0
            ]
        else:
            top_models = []
        rows.append({
            "repo_branch":     f"{repo}@{branch}",
            "repository":      repo,
            "branch":          branch,
            "sessions":        b["sessions"],
            "premium_reqs":    None if b["premium_unknown"] else b["premium_reqs"],
            "est_cost_usd":    b["est_cost_usd"] if b["cost_sessions"] > 0 else None,
            "cost_partial":    b["cost_partial"] and b["cost_sessions"] > 0,
            "top_models":      top_models,
            "top_model":       top_models[0]["name"] if top_models else None,
        })
    return rows


def render_html(records: list[dict], aggregated: dict[str, dict], title: str = "") -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    page_title = f"Cost Dashboard — {title}" if title else "Cost Dashboard"

    def esc(s: str) -> str:
        return html_module.escape(str(s))

    aggregated.pop("_unlinked_summary", None)  # no longer shown; sessions live in the Unlinked project

    # Top-10 by premium requests
    by_premium = sorted(
        ((k, v) for k, v in aggregated.items() if k != "unlinked"),
        key=lambda x: -(x[1]["premium_reqs"] or 0),
    )[:TOP_N]

    # Top-10 by estimated cost
    by_cost = sorted(
        ((k, v) for k, v in aggregated.items() if k != "unlinked"),
        key=lambda x: -(x[1]["est_cost_usd"] or 0.0),
    )[:TOP_N]

    def issue_table(rows: list[tuple], max_cost: float = 0.0) -> str:
        html = ['<table><thead><tr>',
                '<th>Issue</th><th>Sessions</th>',
                '<th>Premium Req</th><th>Est Cost USD</th><th>Confidence</th><th>Top Model</th>',
                '</tr></thead><tbody>']
        for key, g in rows:
            cost_val = g.get("est_cost_usd") or 0.0
            pct = int(cost_val / max_cost * 100) if max_cost > 0 else 0
            cost_str = esc(_fmt_cost(g["est_cost_usd"], g.get("cost_partial", False)))
            bar = (f'<div class="bar-wrap"><div class="bar-fill" style="width:{pct}%"></div>'
                   f'<span class="bar-label">{cost_str}</span></div>')
            tm = g.get("top_models") or []
            if tm:
                top_model_html = " ".join(
                    f'<span class="model-tag">{esc(m["name"])}</span><span class="model-share">{m["share"]}%</span>'
                    for m in tm
                )
            else:
                top_model_html = _DASH
            html.append(
                f'<tr><td>{esc(key)}</td>'
                f'<td class="num">{esc(g["sessions"])}</td>'
                f'<td class="num">{esc(_fmt_premium(g["premium_reqs"]))}</td>'
                f'<td>{bar}</td>'
                f'<td class="conf conf-{esc(g["confidence"])}">'
                f'<span class="dot dot-{esc(g["confidence"])}"></span>{esc(g["confidence"])}</td>'
                f'<td class="model-cell">{top_model_html}</td>'
                f'</tr>'
            )
        html.append('</tbody></table>')
        return "\n".join(html)

    # Period and repo/branch breakdowns
    by_month   = aggregate_by_period(records, "month")
    by_week    = aggregate_by_period(records, "week")
    by_year    = aggregate_by_period(records, "year")
    by_repo_br = aggregate_by_repo_branch(records)

    # ── Model / token aggregations (from records directly — avoids issue double-count) ─
    model_rows = aggregate_by_model(records)
    # Global token totals from records
    _tok_total_in  = sum((r.get("model_metrics") and sum(v.get("input_tokens",0) or 0 for v in r["model_metrics"].values()) or 0) for r in records)
    _tok_total_out = sum((r.get("model_metrics") and sum(v.get("output_tokens",0) or 0 for v in r["model_metrics"].values()) or 0) for r in records)
    _tok_total_cache = sum((r.get("model_metrics") and sum(v.get("cache_read_tokens",0) or 0 for v in r["model_metrics"].values()) or 0) for r in records)
    total_tokens_raw = _tok_total_in + _tok_total_out
    token_coverage   = sum(1 for r in records if r.get("model_metrics"))

    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000_000:
            return f"{n/1_000_000_000:.1f}B"
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return str(n)

    # Model palette — assign a colour per model family
    _MODEL_COLORS = {
        "claude": "#58a6ff",   # blue
        "gpt":    "#3fb950",   # green
        "gemini": "#d29922",   # yellow
    }
    def _model_color(model_id: str) -> str:
        m = model_id.lower()
        for prefix, col in _MODEL_COLORS.items():
            if m.startswith(prefix):
                return col
        return "#8b949e"

    # ── Pie chart data: models per sessions / issues / cost ─────────────────
    import json as _json_pie
    # Per-sessions: use model_rows (already aggregated from records)
    pie_sessions_json = _json_pie.dumps([
        {"label": m["display_name"], "value": m["sessions"], "color": _model_color(m["model_id"])}
        for m in model_rows if m["sessions"] > 0
    ])
    # Per-issues: split each issue fractionally across its top_models by request share
    _issue_model: dict[str, float] = {}
    for _k, _g in aggregated.items():
        if _k.startswith("_"):
            continue
        _tms = _g.get("top_models") or []
        if not _tms:
            continue
        _total_share = sum(m["share"] for m in _tms) or 1
        for m in _tms:
            _issue_model[m["name"]] = _issue_model.get(m["name"], 0.0) + m["share"] / _total_share
    pie_issues_json = _json_pie.dumps(
        sorted([
            {"label": tm, "value": round(cnt, 3),
             "color": _model_color(next((m["model_id"] for m in model_rows if m["display_name"] == tm), ""))}
            for tm, cnt in _issue_model.items()
        ], key=lambda x: -x["value"])
    )
    # Per-cost: split each session's cost proportionally across its top 3 models by request share
    _model_cost: dict[str, float] = {}
    for _rec in records:
        _mm = _rec.get("model_metrics") or {}
        _c = _rec.get("est_cost_usd")
        if _c is None or not _mm:
            continue
        _total_reqs = sum((_mm[m].get("requests_count") or 0) for m in _mm)
        if _total_reqs == 0:
            continue
        for _mid in sorted(_mm, key=lambda m: -(_mm[m].get("requests_count") or 0))[:3]:
            _reqs = _mm[_mid].get("requests_count") or 0
            if _reqs == 0:
                continue
            _disp = _short_model_name(_mid)
            _model_cost[_disp] = _model_cost.get(_disp, 0.0) + _c * _reqs / _total_reqs
    pie_cost_json = _json_pie.dumps(
        sorted([
            {"label": d, "value": round(v, 4),
             "color": _model_color(next((m["model_id"] for m in model_rows if m["display_name"] == d), ""))}
            for d, v in _model_cost.items()
        ], key=lambda x: -x["value"])
    )

    # ── Activity heatmap (last 91 days = 13 weeks) ──────────────────────────
    from datetime import date, timedelta
    today = date.today()
    day_counts: dict[str, int] = {}
    for rec in records:
        d = (rec.get("started_at") or "")[:10]
        if d:
            day_counts[d] = day_counts.get(d, 0) + 1
    # Build 13×7 grid (col=week, row=weekday Mon=0)
    # start from the Monday 12 full weeks ago
    start = today - timedelta(days=today.weekday() + 13 * 7)
    heatmap_cells = []
    max_day = max(day_counts.values()) if day_counts else 1
    CW, CH, GAP = 11, 11, 2  # cell width/height/gap
    COLS, ROWS = 13, 7
    SVG_W = COLS * (CW + GAP)
    SVG_H = ROWS * (CH + GAP) + 20  # +20 for month labels
    month_labels = []
    prev_month = None
    for col in range(COLS):
        for row in range(ROWS):
            d = start + timedelta(days=col * 7 + row)
            ds = d.strftime("%Y-%m-%d")
            count = day_counts.get(ds, 0)
            intensity = min(count / max(max_day, 1), 1.0)
            # colour: 0→#161b22, low→#0e4429, mid→#26a641, high→#39d353
            if count == 0:
                level = 0
            elif intensity < 0.33:
                level = 1
            elif intensity < 0.66:
                level = 2
            else:
                level = 3
            x = col * (CW + GAP)
            y = row * (CH + GAP) + 18
            title = f"{ds}: {count} session{'s' if count != 1 else ''}"
            heatmap_cells.append(
                f'<rect x="{x}" y="{y}" width="{CW}" height="{CH}" '
                f'rx="2" class="hm-{level}"><title>{esc(title)}</title></rect>'
            )
        # month label at first col of that month
        first_day = start + timedelta(days=col * 7)
        m = first_day.strftime("%b")
        if m != prev_month:
            month_labels.append(f'<text x="{col*(CW+GAP)}" y="12" class="hm-month-label" font-size="10">{m}</text>')
            prev_month = m

    heatmap_svg = (
        f'<svg width="{SVG_W}" height="{SVG_H}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(month_labels)
        + "".join(heatmap_cells)
        + "</svg>"
    )

    # ── Repo/branch table with progress bars ────────────────────────────────
    max_rb_cost = max((r.get("est_cost_usd") or 0.0 for r in by_repo_br), default=1.0)
    max_rb_cost = max(max_rb_cost, 0.01)

    def repo_branch_table(rows: list[dict]) -> str:
        html = ['<section><h2>By Repository / Branch</h2>',
                '<table><thead><tr>',
                '<th>Repository</th><th>Branch</th><th>Sessions</th>',
                '<th>Premium Req</th><th>Est Cost USD</th><th>Top Model</th>',
                '</tr></thead><tbody>']
        for r in rows:
            cost_val = r.get("est_cost_usd") or 0.0
            pct = int(cost_val / max_rb_cost * 100)
            cost_str = esc(_fmt_cost(r["est_cost_usd"], r["cost_partial"]))
            bar = (f'<div class="bar-wrap"><div class="bar-fill" style="width:{pct}%"></div>'
                   f'<span class="bar-label">{cost_str}</span></div>')
            tm = r.get("top_models") or []
            if tm:
                top_model_html = " ".join(
                    f'<span class="model-tag">{esc(m["name"])}</span><span class="model-share">{m["share"]}%</span>'
                    for m in tm
                )
            else:
                top_model_html = _DASH
            html.append(
                f'<tr><td>{esc(r["repository"])}</td>'
                f'<td class="mono">{esc(r["branch"])}</td>'
                f'<td class="num">{esc(r["sessions"])}</td>'
                f'<td class="num">{esc(_fmt_premium(r["premium_reqs"]))}</td>'
                f'<td>{bar}</td>'
                f'<td class="model-cell">{top_model_html}</td>'
                f'</tr>'
            )
        html.append('</tbody></table></section>')
        return "\n".join(html)

    # Session timeline — embed as JSON for interactive chart
    import json as _json
    timeline_data = []
    for rec in records:
        tok = _summarize_record_tokens(rec)
        tt = tok["input_tokens"] + tok["output_tokens"] if tok["has_metrics"] else None
        # Best single model for this session
        mm = rec.get("model_metrics") or {}
        if mm:
            total_reqs = sum((mm[m].get("requests_count") or 0) for m in mm)
            top3 = sorted(mm, key=lambda m: -(mm[m].get("requests_count") or 0))[:3]
            top_models_list = [
                {"name": _short_model_name(m),
                 "share": int((mm[m].get("requests_count") or 0) / total_reqs * 100) if total_reqs else 0}
                for m in top3 if (mm[m].get("requests_count") or 0) > 0
            ]
            best_m = top3[0] if top3 else None
        else:
            top_models_list, best_m = [], None
        timeline_data.append({
            "id":         (rec.get("session_id") or "")[:8],
            "date":       (rec.get("started_at") or "")[:10] or None,
            "repo":       rec.get("repository") or "—",
            "branch":     rec.get("branch") or "—",
            "premium":    rec.get("total_premium_requests"),
            "cost":       rec.get("est_cost_usd"),
            "conf":       rec.get("confidence", "unknown"),
            "issues":     [f"#{r['issue']}" for r in (rec.get("issue_refs") or [])],
            "top_models": top_models_list,
            "tokens":     tt,
        })
    timeline_json = _json.dumps(timeline_data)

    # Summary stats scoped to the aggregated set (matches project filter if active).
    # Exclude metadata keys like _unlinked_summary from the totals.
    agg_values = [v for k, v in aggregated.items() if not k.startswith("_")]
    total_sessions = sum(v.get("sessions") or 0 for v in agg_values)
    total_premium  = sum(v.get("premium_reqs") or 0 for v in agg_values)
    has_cost       = any(v.get("est_cost_usd") is not None for v in agg_values)
    cost_partial   = has_cost and any(v.get("est_cost_usd") is None for v in agg_values)
    total_cost_raw = sum(v.get("est_cost_usd") or 0.0 for v in agg_values)
    total_cost_str = _fmt_cost(total_cost_raw, cost_partial) if has_cost else _DASH
    issues_tracked = len(agg_values)

    max_issue_cost = max((v.get("est_cost_usd") or 0.0 for v in aggregated.values()), default=0.01)
    max_issue_cost = max(max_issue_cost, 0.01)

    import json as _json

    def _period_json(rows: list[dict]) -> str:
        return _json.dumps([
            {
                "period":         r["period"],
                "cost":           r.get("est_cost_usd") or 0.0,
                "sessions":       r.get("sessions") or 0,
                "total_tokens":   r.get("total_tokens") or 0,
                "token_sessions": r.get("token_sessions") or 0,
            }
            for r in sorted(rows, key=lambda r: r.get("period", ""))
        ])

    monthly_json = _period_json(by_month)
    weekly_json  = _period_json(by_week)
    yearly_json  = _period_json(by_year)
    model_json   = _json.dumps(model_rows)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page_title)}</title>
<style>
  :root {{
    --bg: #0a0e14; --surface: #111820; --surface2: #161d27;
    --border: #1f2d3d; --text: #cdd9e5; --muted: #636e7b;
    --accent: #58a6ff; --accent2: #79c0ff; --green: #3fb950;
    --yellow: #d29922; --red: #f85149; --unknown: #636e7b;
    --hm-0: #1e2530; --hm-1: #0e4429; --hm-2: #26a641; --hm-3: #39d353;
  }}
  [data-theme="light"] {{
    --bg: #f6f8fa; --surface: #ffffff; --surface2: #f0f2f4;
    --border: #d0d7de; --text: #1f2328; --muted: #656d76;
    --accent: #0969da; --accent2: #218bff; --green: #1a7f37;
    --yellow: #9a6700; --red: #d1242f; --unknown: #656d76;
    --hm-0: #ebedf0; --hm-1: #9be9a8; --hm-2: #40c463; --hm-3: #216e39;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; padding: 24px; max-width: 1400px; margin: 0 auto; }}

  /* ── Header ── */
  .header {{ display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }}
  h1 {{ font-size: 22px; font-weight: 700; color: var(--accent);
    text-shadow: 0 0 20px rgba(88,166,255,0.4); letter-spacing: -0.3px; }}
  .meta {{ color: var(--muted); margin-bottom: 20px; font-size: 12px; }}
  .theme-btn {{ background: none; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); cursor: pointer; font-size: 16px; padding: 4px 10px;
    transition: border-color 0.2s, color 0.2s; }}
  .theme-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* ── Stat cards ── */
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px; margin-bottom: 28px; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px;
    box-shadow: 0 0 0 0 rgba(88,166,255,0); transition: box-shadow 0.3s;
    position: relative; overflow: hidden; }}
  .stat::before {{ content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6; }}
  .stat:hover {{ box-shadow: 0 0 16px rgba(88,166,255,0.15); }}
  .stat-value {{ font-size: 28px; font-weight: 700; color: var(--accent);
    font-variant-numeric: tabular-nums; }}
  .stat-label {{ font-size: 11px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.08em; }}

  /* ── Sections ── */
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 13px; font-weight: 600; color: var(--muted);
    margin-bottom: 12px; padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.1em; }}

  /* ── Tables ── */
  table {{ width: 100%; border-collapse: collapse; background: var(--surface);
    border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  th, td {{ padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); background: var(--bg); font-weight: 500; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: rgba(88,166,255,0.04); }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, monospace; font-size: 13px; }}
  .mono {{ font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); }}

  /* ── Confidence dots ── */
  .dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 5px; vertical-align: middle; }}
  .dot-exact {{ background: var(--green); box-shadow: 0 0 5px var(--green); }}
  .dot-low {{ background: var(--yellow); box-shadow: 0 0 5px var(--yellow); }}
  .dot-unknown {{ background: var(--muted); }}
  .conf-exact {{ color: var(--green); }}
  .conf-low {{ color: var(--yellow); }}
  .conf-unknown {{ color: var(--unknown); }}

  /* ── Bar cells ── */
  .bar-wrap {{ position: relative; height: 20px; background: var(--surface2);
    border-radius: 3px; overflow: hidden; min-width: 80px; }}
  .bar-fill {{ position: absolute; top: 0; left: 0; height: 100%;
    background: linear-gradient(90deg, rgba(88,166,255,0.5), rgba(88,166,255,0.25));
    border-radius: 3px; transition: width 0.8s ease; }}
  .bar-label {{ position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
    font-size: 12px; font-family: ui-monospace, monospace;
    color: var(--text); white-space: nowrap; }}

  /* ── Heatmap ── */
  .heatmap-wrap {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; }}
  .heatmap-legend {{ display: flex; align-items: center; gap: 4px;
    margin-top: 8px; font-size: 11px; color: var(--muted); }}
  .heatmap-legend span {{ display: inline-block; width: 11px; height: 11px; border-radius: 2px; }}
  .hm-0 {{ fill: var(--hm-0); background: var(--hm-0); }}
  .hm-1 {{ fill: var(--hm-1); background: var(--hm-1); }}
  .hm-2 {{ fill: var(--hm-2); background: var(--hm-2); }}
  .hm-3 {{ fill: var(--hm-3); background: var(--hm-3); }}
  .hm-month-label {{ fill: var(--muted); }}

  /* ── Layout grids ── */
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
  .three-col {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
  @media (max-width: 1100px) {{ .three-col {{ grid-template-columns: 1fr 1fr; }} }}
  @media (max-width: 700px) {{ .two-col, .three-col {{ grid-template-columns: 1fr; }} }}

  /* ── Warning ── */
  .warning {{ background: color-mix(in srgb, var(--yellow) 15%, var(--bg)); border: 1px solid var(--yellow);
    border-radius: 6px; padding: 10px 16px; margin-bottom: 20px;
    color: var(--yellow); font-size: 13px; }}

  /* ── Chart panels ── */
  .chart-panel {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; overflow-x: auto; }}
  .chart-panel h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 12px; }}
  .combo-chart {{ width: 100%; min-height: 180px; }}
  .combo-chart svg {{ display: block; width: 100%; }}

  /* ── Timeline chart ── */
  #timeline-chart {{ width: 100%; height: 220px; position: relative;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden; cursor: crosshair; }}
  #timeline-chart svg {{ width: 100%; height: 100%; display: block; }}
  #tl-tooltip {{ position: fixed; display: none; pointer-events: none;
    background: var(--surface); border: 1px solid var(--accent);
    border-radius: 6px; padding: 10px 14px; font-size: 12px; line-height: 1.6;
    box-shadow: 0 4px 20px rgba(0,0,0,0.6), 0 0 10px rgba(88,166,255,0.15);
    max-width: 300px; z-index: 999; }}
  .tt-id   {{ font-family: ui-monospace,monospace; color: var(--accent2); font-weight:600; }}
  .tt-key  {{ color: var(--muted); font-size: 11px; }}
  .tt-val  {{ color: var(--text); }}
  .tt-conf-exact   {{ color: var(--green); }}
  .tt-conf-low     {{ color: var(--yellow); }}
  .tt-conf-unknown {{ color: var(--muted); }}
  .tl-axis-label {{ font-size: 10px; fill: var(--muted); font-family: ui-monospace,monospace; }}

  /* ── Model Intelligence cards ── */
  .model-cards {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 4px; }}
  .model-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 18px; min-width: 200px; flex: 1; position: relative; overflow: hidden;
  }}
  .model-card::before {{ content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 3px; }}
  .model-card-name {{ font-size: 15px; font-weight: 700; margin-bottom: 10px; }}
  .model-card-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }}
  .mc-val {{ font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }}
  .mc-label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}

  /* ── Model tag in issue table ── */
  .model-cell {{ white-space: nowrap; }}
  .model-tag {{ font-size: 11px; font-family: ui-monospace,monospace;
    background: rgba(88,166,255,0.12); border-radius: 3px; padding: 1px 5px;
    color: var(--accent2); }}
  .model-share {{ font-size: 10px; color: var(--muted); margin-left: 4px; }}

  /* ── Token combo chart ── */
  .tok-combo-chart {{ width: 100%; min-height: 180px; }}

  /* ── Pie charts ── */
  .pie-chart-wrap {{ width: 100%; min-height: 200px; }}
  .pie-chart-wrap svg {{ display: block; width: 100%; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>⬡ {esc(page_title)}</h1>
  </div>
  <button class="theme-btn" id="theme-btn" title="Toggle theme">☀</button>
</div>
<p class="meta">Generated {esc(now_utc)} &nbsp;·&nbsp; {esc(str(total_sessions))} session(s) loaded</p>

<div class="stats">
  <div class="stat">
    <div class="stat-value" data-count="{total_sessions}">0</div>
    <div class="stat-label">Sessions</div>
  </div>
  <div class="stat">
    <div class="stat-value" data-count="{total_premium}">0</div>
    <div class="stat-label">Premium Requests</div>
  </div>
  <div class="stat">
    <div class="stat-value">{esc(total_cost_str)}</div>
    <div class="stat-label">Est. Cost (USD)</div>
  </div>
  <div class="stat">
    <div class="stat-value" data-count="{issues_tracked}">0</div>
    <div class="stat-label">Issues Tracked</div>
  </div>
  <div class="stat">
    <div class="stat-value">{esc(_fmt_tokens(total_tokens_raw))}</div>
    <div class="stat-label">Tokens (in+out)</div>
  </div>
  <div class="stat">
    <div class="stat-value">{token_coverage}/{len(records)}</div>
    <div class="stat-label">Sessions w/ Metrics</div>
  </div>
</div>

<section>
  <h2>Activity — Last 13 Weeks</h2>
  <div class="heatmap-wrap">
    {heatmap_svg}
    <div class="heatmap-legend">
      Less &nbsp;
      <span class="hm-0"></span>
      <span class="hm-1"></span>
      <span class="hm-2"></span>
      <span class="hm-3"></span>
      &nbsp; More
    </div>
  </div>
</section>

<div class="three-col">
  <div class="chart-panel">
    <h3>Monthly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-monthly" class="combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Weekly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-weekly" class="combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Yearly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-yearly" class="combo-chart"></div>
  </div>
</div>

<div class="three-col">
  <div class="chart-panel">
    <h3>Monthly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-monthly" class="tok-combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Weekly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-weekly" class="tok-combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Yearly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-yearly" class="tok-combo-chart"></div>
  </div>
</div>

<section id="model-intelligence">
  <h2>Model Intelligence</h2>
  <div class="model-cards" id="model-cards-container"></div>
</section>

<div class="three-col">
  <div class="chart-panel">
    <h3>Models by Sessions</h3>
    <div id="pie-models-sessions" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Issues</h3>
    <div id="pie-models-issues" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Cost (attributed)</h3>
    <div id="pie-models-cost" class="pie-chart-wrap"></div>
  </div>
</div>

<div class="two-col">
  <section>
    <h2>Top {TOP_N} Issues — Premium Requests</h2>
    {issue_table(by_premium, max_issue_cost)}
  </section>
  <section>
    <h2>Top {TOP_N} Issues — Estimated Cost</h2>
    {issue_table(by_cost, max_issue_cost)}
  </section>
</div>

{repo_branch_table(by_repo_br)}

<section>
  <h2>Session Timeline — {esc(str(total_sessions))} sessions · only sessions with known cost shown</h2>
  <div id="timeline-chart"><svg id="tl-svg"></svg></div>
</section>

<script>
  // ── Theme ────────────────────────────────────────────────────────────────
  (function initTheme() {{
    const stored = localStorage.getItem('copilot-cost-theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = stored || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '🌙';
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {{
      if (!localStorage.getItem('copilot-cost-theme')) {{
        const t = e.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', t);
        if (btn) btn.textContent = t === 'dark' ? '☀' : '🌙';
        if (typeof redrawAll === 'function') redrawAll();
      }}
    }});
    if (btn) btn.addEventListener('click', () => {{
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('copilot-cost-theme', next);
      btn.textContent = next === 'dark' ? '☀' : '🌙';
      if (typeof redrawAll === 'function') redrawAll();
    }});
  }})();

  function getColors() {{
    const s = getComputedStyle(document.documentElement);
    const g = n => s.getPropertyValue(n).trim();
    return {{
      bg: g('--bg'), surface: g('--surface'), surface2: g('--surface2'),
      border: g('--border'), text: g('--text'), muted: g('--muted'),
      accent: g('--accent'), accent2: g('--accent2'), green: g('--green'),
      yellow: g('--yellow'), red: g('--red'),
    }};
  }}

  function hexAlpha(hex, a) {{
    const r = parseInt(hex.slice(1,3),16), g2 = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return `rgba(${{r}},${{g2}},${{b}},${{a}})`;
  }}


  // Animated counters
  document.querySelectorAll('[data-count]').forEach(el => {{
    const target = parseInt(el.dataset.count, 10);
    if (isNaN(target) || target === 0) {{ el.textContent = '0'; return; }}
    const duration = 800, steps = 40, step = Math.ceil(target / steps);
    let current = 0;
    const timer = setInterval(() => {{
      current = Math.min(current + step, target);
      el.textContent = current.toLocaleString();
      if (current >= target) clearInterval(timer);
    }}, duration / steps);
  }});

  // ── Timeline bar chart ──────────────────────────────────────────────────
  const SESSIONS = {timeline_json};

  function fmtCost(v) {{
    if (v == null) return '—';
    return '$' + v.toFixed(3);
  }}

  function renderTimeline() {{
    const C = getColors();
    const CONF_COLOR = {{
      exact:   hexAlpha(C.green, 0.85),
      low:     hexAlpha(C.yellow, 0.85),
      unknown: hexAlpha(C.muted, 0.6),
    }};
    const CONF_COLOR_HOVER = {{
      exact:   C.green,
      low:     C.yellow,
      unknown: C.muted,
    }};
    const container = document.getElementById('timeline-chart');
    const W = container.clientWidth || 1200;
    const H = 220;
    const PAD = {{ t: 20, b: 28, l: 8, r: 8 }};
    const chartH = H - PAD.t - PAD.b;
    const chartW = W - PAD.l - PAD.r;

    const SLOT = 8;
    const BAR_W = 5;
    const maxVisible = Math.floor(chartW / SLOT);
    const allSess = SESSIONS.slice()
      .filter(s => s.cost != null)
      .sort((a,b) => (a.date||'').localeCompare(b.date||''));
    const sess = allSess.slice(-maxVisible);
    const n = sess.length;
    if (n === 0) return;

    const maxCost = Math.max(...sess.map(s => s.cost || 0), 0.001);
    const maxPrem = Math.max(...sess.map(s => s.premium || 0), 1);

    function barHeight(s) {{
      if (s.cost != null) return Math.max(3, Math.round(s.cost / maxCost * chartH));
      if (s.premium != null) return Math.max(2, Math.round(s.premium / maxPrem * chartH * 0.4));
      return 2;
    }}

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    let html = `<svg id="tl-svg" width="${{W}}" height="${{H}}" xmlns="http://www.w3.org/2000/svg">`;
    html += `<line x1="${{PAD.l}}" y1="${{H-PAD.b}}" x2="${{W-PAD.r}}" y2="${{H-PAD.b}}" stroke="${{C.border}}" stroke-width="1"/>`;

    const gridY = PAD.t + Math.round(chartH * 0.5);
    html += `<line x1="${{PAD.l}}" y1="${{gridY}}" x2="${{W-PAD.r}}" y2="${{gridY}}" stroke="${{C.border}}" stroke-width="1" stroke-dasharray="3,3"/>`;

    sess.forEach((s, i) => {{
      const x = PAD.l + i * SLOT;
      const bh = barHeight(s);
      const y = H - PAD.b - bh;
      const color = CONF_COLOR[s.conf] || CONF_COLOR.unknown;
      html += `<rect class="tl-bar" data-idx="${{i}}" x="${{x}}" y="${{y}}" width="${{BAR_W}}" height="${{bh}}" rx="1" fill="${{color}}" opacity="0.9"/>`;
    }});

    let lastLabelX = -999;
    let lastYM = '';
    sess.forEach((s, i) => {{
      if (!s.date) return;
      const ym = s.date.slice(0, 7);
      if (ym === lastYM) return;
      lastYM = ym;
      const x = PAD.l + i * SLOT + BAR_W / 2;
      if (x - lastLabelX < 60) return;
      const [yr, mo] = ym.split('-');
      const label = MONTHS[parseInt(mo,10)-1] + " '" + yr.slice(2);
      html += `<line x1="${{x}}" y1="${{H-PAD.b}}" x2="${{x}}" y2="${{H-PAD.b+4}}" stroke="${{C.muted}}" stroke-width="1"/>`;
      html += `<text class="tl-axis-label" x="${{x}}" y="${{H-4}}" text-anchor="middle">${{label}}</text>`;
      lastLabelX = x;
    }});

    const hiddenCount = allSess.length - n;
    html += `<text class="tl-axis-label" x="${{PAD.l+2}}" y="${{PAD.t-4}}">max ${{fmtCost(maxCost)}}</text>`;
    if (hiddenCount > 0) {{
      html += `<text class="tl-axis-label" x="${{W-PAD.r-2}}" y="${{PAD.t-4}}" text-anchor="end">showing last ${{n}} of ${{allSess.length}} sessions</text>`;
    }}

    html += '</svg>';
    container.innerHTML = html + '<div id="tl-tooltip"></div>';

    const newSvg = container.querySelector('svg');
    if (!newSvg) return;
    const tt = document.getElementById('tl-tooltip');

    newSvg.querySelectorAll('.tl-bar').forEach(bar => {{
      const s = sess[parseInt(bar.dataset.idx, 10)];
      bar.addEventListener('mouseenter', (e) => {{
        const conf = s.conf || 'unknown';
        const confColor = CONF_COLOR_HOVER[conf] || C.muted;
        bar.setAttribute('opacity', '1');
        bar.setAttribute('fill', confColor);
        const issues = s.issues && s.issues.length ? s.issues.join(', ') : '—';
        const modelLines = (s.top_models && s.top_models.length)
          ? s.top_models.map((m, i) =>
              `<div><span class="tt-key">${{i === 0 ? 'Models&nbsp;&nbsp;' : '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'}}</span><span class="tt-val" style="color:${{C.accent}}">${{m.name}}</span><span style="color:${{C.muted}};font-size:10px"> ${{m.share}}%</span></div>`
            ).join('')
          : '';
        const fmtTok = v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : String(v);
        const tokLine = s.tokens != null ? `<div><span class="tt-key">Tokens&nbsp;&nbsp;</span><span class="tt-val">${{fmtTok(s.tokens)}}</span></div>` : '';
        tt.innerHTML =
          `<div class="tt-id">${{s.id}}</div>` +
          `<div><span class="tt-key">Date&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${{s.date || '—'}}</span></div>` +
          `<div><span class="tt-key">Repo&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${{s.repo}}</span></div>` +
          `<div><span class="tt-key">Branch&nbsp;&nbsp;</span><span class="tt-val">${{s.branch}}</span></div>` +
          `<div><span class="tt-key">Premium&nbsp;</span><span class="tt-val">${{s.premium != null ? s.premium : '—'}}</span></div>` +
          `<div><span class="tt-key">Cost&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${{fmtCost(s.cost)}}</span></div>` +
          `<div><span class="tt-key">Conf&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-conf-${{conf}}">${{conf}}</span></div>` +
          `<div><span class="tt-key">Issues&nbsp;&nbsp;</span><span class="tt-val">${{issues}}</span></div>` +
          modelLines + tokLine;
        tt.style.display = 'block';
        moveTooltip(e);
      }});
      bar.addEventListener('mousemove', moveTooltip);
      bar.addEventListener('mouseleave', () => {{
        bar.setAttribute('opacity', '0.9');
        bar.setAttribute('fill', CONF_COLOR[s.conf] || CONF_COLOR.unknown);
        tt.style.display = 'none';
      }});
    }});

    function moveTooltip(e) {{
      const vpW = window.innerWidth, vpH = window.innerHeight;
      const ttW = 280, ttH = 170;
      let x = e.clientX + 16, y = e.clientY + 16;
      if (x + ttW > vpW - 8) x = e.clientX - ttW - 8;
      if (y + ttH > vpH - 8) y = e.clientY - ttH - 8;
      tt.style.left = x + 'px';
      tt.style.top  = y + 'px';
    }}
  }}

  // ── Combo charts (cost bars + sessions curve) ─────────────────────────
  const MONTHLY_DATA = {monthly_json};
  const WEEKLY_DATA  = {weekly_json};
  const YEARLY_DATA  = {yearly_json};

  function drawComboChart(containerId, data) {{
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container || !data.length) return;

    const W = container.clientWidth || 600;
    const H = 180;
    const PAD = {{ t: 24, b: 32, l: 56, r: 52 }};
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = data.length;

    const maxCost = Math.max(...data.map(d => d.cost), 0.001);
    const maxSess = Math.max(...data.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, slotW * 0.5);
    const cx = i => PAD.l + i * slotW + slotW / 2;
    const sessY  = v => PAD.t + cH - (v / maxSess * cH);
    const fmtC   = v => '$' + v.toFixed(2);
    const fmtS   = v => String(Math.round(v));

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {{
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${{PAD.l}}" y1="${{gy.toFixed(1)}}" x2="${{PAD.l+cW}}" y2="${{gy.toFixed(1)}}"
              stroke="${{C.border}}" stroke-width="1" ${{frac < 1 ? 'stroke-dasharray="3,3"' : ''}}/>`;
      s += `<text x="${{PAD.l-6}}" y="${{(gy+4).toFixed(1)}}" text-anchor="end"
              fill="${{C.accent}}" font-size="10">${{fmtC(maxCost * frac)}}</text>`;
      s += `<text x="${{PAD.l+cW+6}}" y="${{(gy+4).toFixed(1)}}"
              fill="${{C.green}}" font-size="10">${{fmtS(maxSess * frac)}}</text>`;
    }}

    s += `<line x1="${{PAD.l}}" y1="${{PAD.t}}" x2="${{PAD.l}}" y2="${{PAD.t+cH}}"
            stroke="${{C.border}}" stroke-width="1"/>`;

    data.forEach((d, i) => {{
      const bh = Math.max(2, d.cost / maxCost * cH);
      const bx = cx(i) - barW / 2;
      const by = PAD.t + cH - bh;
      s += `<rect x="${{bx.toFixed(1)}}" y="${{by.toFixed(1)}}"
              width="${{barW.toFixed(1)}}" height="${{bh.toFixed(1)}}"
              rx="2" fill="${{hexAlpha(C.accent, 0.65)}}"/>`;
    }});

    const pts = data.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {{
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {{
        const dx = pts[i+1][0] - pts[i][0];
        const dy = pts[i+1][1] - pts[i][1];
        const slope = dy / dx;
        if (i === 0) m[0] = slope;
        else if (i === pts.length - 2) m[pts.length-1] = slope;
        m[i === 0 ? 0 : i] = slope;
        m[i+1] = slope;
      }}
      for (let i = 1; i < pts.length - 1; i++) {{
        const s0 = (pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]);
        const s1 = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = (s0 + s1) / 2;
      }}
      let path = `M ${{pts[0][0].toFixed(1)}} ${{pts[0][1].toFixed(1)}}`;
      for (let i = 0; i < pts.length - 1; i++) {{
        const dx = (pts[i+1][0] - pts[i][0]) / 3;
        const cp1x = pts[i][0] + dx, cp1y = pts[i][1] + m[i] * dx;
        const cp2x = pts[i+1][0] - dx, cp2y = pts[i+1][1] - m[i+1] * dx;
        path += ` C ${{cp1x.toFixed(1)}} ${{cp1y.toFixed(1)}} ${{cp2x.toFixed(1)}} ${{cp2y.toFixed(1)}} ${{pts[i+1][0].toFixed(1)}} ${{pts[i+1][1].toFixed(1)}}`;
      }}
      const fillPath = path +
        ` L ${{pts[pts.length-1][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}}` +
        ` L ${{pts[0][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} Z`;
      s += `<path d="${{fillPath}}" fill="${{hexAlpha(C.green, 0.08)}}"/>`;
      s += `<path d="${{path}}" fill="none" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
      pts.forEach(([px, py]) => {{
        s += `<circle cx="${{px.toFixed(1)}}" cy="${{py.toFixed(1)}}" r="3" fill="${{C.green}}"/>`;
      }});
    }}

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function fmtPeriod(p) {{
      const wm = p.match(/^\d{{4}}-W(\d+)$/);
      if (wm) return 'W' + wm[1];
      const mm = p.match(/^\d{{4}}-(\d{{2}})$/);
      if (mm) return MONTHS[parseInt(mm[1], 10) - 1] || p;
      return p;
    }}
    data.forEach((d, i) => {{
      s += `<text x="${{cx(i).toFixed(1)}}" y="${{H-6}}" text-anchor="middle"
              fill="${{C.muted}}" font-size="10">${{fmtPeriod(d.period)}}</text>`;
    }});

    s += `<rect x="${{PAD.l}}" y="6" width="10" height="10" rx="2" fill="${{hexAlpha(C.accent, 0.65)}}"/>`;
    s += `<text x="${{PAD.l+14}}" y="15" fill="${{C.accent}}" font-size="10">Cost USD</text>`;
    s += `<line x1="${{PAD.l+72}}" y1="11" x2="${{PAD.l+88}}" y2="11" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
    s += `<circle cx="${{PAD.l+80}}" cy="11" r="3" fill="${{C.green}}"/>`;
    s += `<text x="${{PAD.l+94}}" y="15" fill="${{C.green}}" font-size="10">Sessions</text>`;
    s += '</svg>';
    container.innerHTML = s;
  }}

  function drawAllCombos() {{
    drawComboChart('chart-monthly', MONTHLY_DATA);
    drawComboChart('chart-weekly',  WEEKLY_DATA);
    drawComboChart('chart-yearly',  YEARLY_DATA);
    drawTokenChart('chart-tok-monthly', MONTHLY_DATA);
    drawTokenChart('chart-tok-weekly',  WEEKLY_DATA);
    drawTokenChart('chart-tok-yearly',  YEARLY_DATA);
  }}
  // ── Token combo charts (total_tokens bars + token_sessions curve) ────────
  function drawTokenChart(containerId, data) {{
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    const filtered = data.filter(d => d.total_tokens > 0);
    if (!filtered.length) {{ container.innerHTML = `<p style="color:${{C.muted}};font-size:12px;padding:16px">No token data</p>`; return; }}

    const W = container.clientWidth || 600;
    const H = 180;
    const PAD = {{ t: 24, b: 32, l: 64, r: 52 }};
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = filtered.length;

    const maxTok  = Math.max(...filtered.map(d => d.total_tokens), 1);
    const maxSess = Math.max(...filtered.map(d => d.token_sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, slotW * 0.5);
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);
    const fmtTok = v => v >= 1e9 ? (v/1e9).toFixed(1)+'B' : v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : String(v);

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function fmtPeriod(p) {{
      const wm = p.match(/^\d{{4}}-W(\d+)$/);
      if (wm) return 'W' + wm[1];
      const mm = p.match(/^\d{{4}}-(\d{{2}})$/);
      if (mm) return MONTHS[parseInt(mm[1], 10) - 1] || p;
      return p;
    }}

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {{
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${{PAD.l}}" y1="${{gy.toFixed(1)}}" x2="${{PAD.l+cW}}" y2="${{gy.toFixed(1)}}"
              stroke="${{C.border}}" stroke-width="1" ${{frac < 1 ? 'stroke-dasharray="3,3"' : ''}}/>`;
      s += `<text x="${{PAD.l-6}}" y="${{(gy+4).toFixed(1)}}" text-anchor="end"
              fill="${{C.yellow}}" font-size="10">${{fmtTok(maxTok * frac)}}</text>`;
      s += `<text x="${{PAD.l+cW+6}}" y="${{(gy+4).toFixed(1)}}"
              fill="${{C.green}}" font-size="10">${{Math.round(maxSess * frac)}}</text>`;
    }}
    s += `<line x1="${{PAD.l}}" y1="${{PAD.t}}" x2="${{PAD.l}}" y2="${{PAD.t+cH}}" stroke="${{C.border}}" stroke-width="1"/>`;

    filtered.forEach((d, i) => {{
      const bh = Math.max(2, d.total_tokens / maxTok * cH);
      const bx = cx(i) - barW / 2;
      const by = PAD.t + cH - bh;
      s += `<rect x="${{bx.toFixed(1)}}" y="${{by.toFixed(1)}}" width="${{barW.toFixed(1)}}" height="${{bh.toFixed(1)}}" rx="2" fill="${{hexAlpha(C.yellow, 0.65)}}"/>`;
    }});

    const pts = filtered.map((d, i) => [cx(i), sessY(d.token_sessions)]);
    if (pts.length >= 2) {{
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {{
        const sl = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = sl; m[i+1] = sl;
      }}
      for (let i = 1; i < pts.length - 1; i++) {{
        const s0 = (pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]);
        const s1 = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = (s0 + s1) / 2;
      }}
      let path = `M ${{pts[0][0].toFixed(1)}} ${{pts[0][1].toFixed(1)}}`;
      for (let i = 0; i < pts.length - 1; i++) {{
        const dx = (pts[i+1][0] - pts[i][0]) / 3;
        const cp1x = pts[i][0] + dx, cp1y = pts[i][1] + m[i] * dx;
        const cp2x = pts[i+1][0] - dx, cp2y = pts[i+1][1] - m[i+1] * dx;
        path += ` C ${{cp1x.toFixed(1)}} ${{cp1y.toFixed(1)}} ${{cp2x.toFixed(1)}} ${{cp2y.toFixed(1)}} ${{pts[i+1][0].toFixed(1)}} ${{pts[i+1][1].toFixed(1)}}`;
      }}
      const fillPath = path + ` L ${{pts[pts.length-1][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} L ${{pts[0][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} Z`;
      s += `<path d="${{fillPath}}" fill="${{hexAlpha(C.green, 0.08)}}"/>`;
      s += `<path d="${{path}}" fill="none" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
      pts.forEach(([px, py]) => s += `<circle cx="${{px.toFixed(1)}}" cy="${{py.toFixed(1)}}" r="3" fill="${{C.green}}"/>`);
    }}

    filtered.forEach((d, i) => {{
      s += `<text x="${{cx(i).toFixed(1)}}" y="${{H-6}}" text-anchor="middle" fill="${{C.muted}}" font-size="10">${{fmtPeriod(d.period)}}</text>`;
    }});

    s += `<rect x="${{PAD.l}}" y="6" width="10" height="10" rx="2" fill="${{hexAlpha(C.yellow, 0.65)}}"/>`;
    s += `<text x="${{PAD.l+14}}" y="15" fill="${{C.yellow}}" font-size="10">Tokens</text>`;
    s += `<line x1="${{PAD.l+60}}" y1="11" x2="${{PAD.l+76}}" y2="11" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
    s += `<circle cx="${{PAD.l+68}}" cy="11" r="3" fill="${{C.green}}"/>`;
    s += `<text x="${{PAD.l+82}}" y="15" fill="${{C.green}}" font-size="10">Sessions w/ Metrics</text>`;
    s += '</svg>';
    container.innerHTML = s;
  }}

  // ── Model Intelligence cards ────────────────────────────────────────────
  const MODEL_DATA = {model_json};
  const MODEL_COLORS = {{ claude: '#58a6ff', gpt: '#3fb950', gemini: '#d29922' }};
  function modelColor(id) {{
    const lo = (id || '').toLowerCase();
    for (const [pfx, col] of Object.entries(MODEL_COLORS)) {{
      if (lo.startsWith(pfx)) return col;
    }}
    return '#8b949e';
  }}
  function fmtTokGlobal(v) {{
    if (v >= 1e9) return (v/1e9).toFixed(2)+'B';
    if (v >= 1e6) return (v/1e6).toFixed(2)+'M';
    if (v >= 1e3) return (v/1e3).toFixed(1)+'K';
    return String(v);
  }}
  (function renderModelCards() {{
    const container = document.getElementById('model-cards-container');
    if (!container || !MODEL_DATA.length) return;
    let html = '';
    MODEL_DATA.forEach(m => {{
      const col = modelColor(m.model_id);
      const totalTok = m.total_tokens || 0;
      const cacheRatio = m.cache_read_tokens && (m.input_tokens + m.cache_read_tokens) > 0
        ? Math.round(m.cache_read_tokens / (m.input_tokens + m.cache_read_tokens) * 100) : 0;
      const premPct = m.requests_count > 0 ? Math.round(m.requests_premium / m.requests_count * 100) : 0;
      html += `
      <div class="model-card">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;background:${{col}}"></div>
        <div class="model-card-name" style="color:${{col}}">${{m.display_name}}</div>
        <div class="model-card-stats">
          <div><div class="mc-val" style="color:${{col}}">${{m.requests_count.toLocaleString()}}</div><div class="mc-label">Requests</div></div>
          <div><div class="mc-val">${{premPct}}%</div><div class="mc-label">Premium %</div></div>
          <div><div class="mc-val">${{fmtTokGlobal(totalTok)}}</div><div class="mc-label">Tokens (in+out)</div></div>
          <div><div class="mc-val">${{cacheRatio}}%</div><div class="mc-label">Cache Hit</div></div>
          <div><div class="mc-val">${{m.sessions}}</div><div class="mc-label">Sessions</div></div>
        </div>
      </div>`;
    }});
    container.innerHTML = html;
  }})();

  // ── Pie charts (models by sessions / issues / cost) ────────────────────
  const PIE_SESSIONS = {pie_sessions_json};
  const PIE_ISSUES   = {pie_issues_json};
  const PIE_COST     = {pie_cost_json};

  function drawPie(containerId, data, fmtVal) {{
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) {{
      container.innerHTML = `<p style="color:${{C.muted}};font-size:12px;padding:16px">No data</p>`;
      return;
    }}
    const W = container.clientWidth || 360;
    const H = 200;
    const R = Math.min(H * 0.42, 78);
    const IR = R * 0.55;
    const CX = R + 16, CY = H / 2;
    const LEGEND_X = CX + R + 22;

    const total = data.reduce((s, d) => s + d.value, 0);
    if (total === 0) {{
      container.innerHTML = `<p style="color:${{C.muted}};font-size:12px;padding:16px">No data</p>`;
      return;
    }}

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    let startAngle = -Math.PI / 2;
    const segments = data.map(d => {{
      const sweep = (d.value / total) * 2 * Math.PI;
      const seg = {{ ...d, startAngle, sweep }};
      startAngle += sweep;
      return seg;
    }});

    function polarXY(angle, r) {{
      return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)];
    }}

    segments.forEach(seg => {{
      if (seg.sweep < 0.001) return;
      const [x1o, y1o] = polarXY(seg.startAngle, R);
      const [x2o, y2o] = polarXY(seg.startAngle + seg.sweep, R);
      const [x1i, y1i] = polarXY(seg.startAngle + seg.sweep, IR);
      const [x2i, y2i] = polarXY(seg.startAngle, IR);
      const large = seg.sweep > Math.PI ? 1 : 0;
      const path =
        `M ${{x1o.toFixed(2)}} ${{y1o.toFixed(2)}}` +
        ` A ${{R}} ${{R}} 0 ${{large}} 1 ${{x2o.toFixed(2)}} ${{y2o.toFixed(2)}}` +
        ` L ${{x1i.toFixed(2)}} ${{y1i.toFixed(2)}}` +
        ` A ${{IR}} ${{IR}} 0 ${{large}} 0 ${{x2i.toFixed(2)}} ${{y2i.toFixed(2)}} Z`;
      s += `<path d="${{path}}" fill="${{seg.color}}" opacity="0.85">
        <title>${{seg.label}}: ${{fmtVal ? fmtVal(seg.value) : seg.value}} (${{Math.round(seg.value/total*100)}}%)</title>
      </path>`;
    }});

    s += `<text x="${{CX}}" y="${{CY - 6}}" text-anchor="middle" fill="${{C.text}}" font-size="14" font-weight="700">${{fmtVal ? fmtVal(total) : total}}</text>`;
    s += `<text x="${{CX}}" y="${{CY + 10}}" text-anchor="middle" fill="${{C.muted}}" font-size="9">total</text>`;

    const legendItemH = 18;
    const legendStartY = CY - (data.length * legendItemH) / 2;
    data.forEach((d, i) => {{
      const ly = legendStartY + i * legendItemH + 8;
      if (ly > H - 4) return;
      const pct = Math.round(d.value / total * 100);
      s += `<rect x="${{LEGEND_X}}" y="${{ly - 8}}" width="10" height="10" rx="2" fill="${{d.color}}" opacity="0.85"/>`;
      s += `<text x="${{LEGEND_X + 14}}" y="${{ly}}" fill="${{C.text}}" font-size="11">${{d.label}}</text>`;
      s += `<text x="${{W - 4}}" y="${{ly}}" text-anchor="end" fill="${{C.muted}}" font-size="10">${{pct}}%</text>`;
    }});

    s += '</svg>';
    container.innerHTML = s;
  }}

  function fmtCostPie(v) {{ return '$' + v.toFixed(2); }}

  function redrawAll() {{
    renderTimeline();
    drawAllCombos();
    drawPie('pie-models-sessions', PIE_SESSIONS, null);
    drawPie('pie-models-issues',   PIE_ISSUES,   null);
    drawPie('pie-models-cost',     PIE_COST,     fmtCostPie);
  }}
  redrawAll();
  window.addEventListener('resize', redrawAll);
</script>
</body>
</html>"""


def render_index_html(projects: list[dict], generated_at: str = "",
                      global_model_data: Optional[list[dict]] = None,
                      global_model_cost: Optional[list[dict]] = None,
                      global_monthly_data: Optional[list[dict]] = None) -> str:
    """
    Render the main index page comparing all projects.

    projects: list of dicts with keys:
        number, title, issues_count, total_sessions, total_premium,
        total_cost, cost_partial, dashboard_url, top_model (optional)
    global_model_data: output of aggregate_by_model(all_records)
    global_model_cost: [{label, value, color}] model-cost attribution
    """
    import json as _json
    esc = html_module.escape
    now_utc = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_cost_all = sum(p.get("total_cost") or 0.0 for p in projects)
    total_sessions_all = sum(p.get("total_sessions") or 0 for p in projects)

    def fmt_cost(v):
        if v is None:
            return "—"
        return f"${v:.2f}"

    # Project cards HTML
    cards_html = []
    for p in sorted(projects, key=lambda x: -(x.get("total_cost") or 0)):
        cost_str = fmt_cost(p.get("total_cost"))
        url = esc(p.get("dashboard_url", "#"))
        title = esc(p.get("title", f"Project #{p['number']}"))
        num = esc(str(p["number"]))
        sessions = esc(str(p.get("total_sessions") or 0))
        premium = esc(str(p.get("total_premium") or "—"))
        issues = esc(str(p.get("issues_count") or 0))
        is_unlinked = p.get("is_unlinked", False)
        card_class = "proj-card proj-card-unlinked" if is_unlinked else "proj-card"
        num_label = "∅ unlinked" if is_unlinked else f"#{num}"
        issues_label = "—" if is_unlinked else issues
        cards_html.append(f"""
  <a class="{card_class}" href="{url}">
    <div class="proj-num">{num_label}</div>
    <div class="proj-title">{title}</div>
    <div class="proj-stats">
      <div class="proj-stat"><span class="proj-val">{esc(cost_str)}</span><span class="proj-key">Est. Cost</span></div>
      <div class="proj-stat"><span class="proj-val" data-count="{sessions}">{sessions}</span><span class="proj-key">Sessions</span></div>
      <div class="proj-stat"><span class="proj-val">{premium}</span><span class="proj-key">Premium Req</span></div>
      <div class="proj-stat"><span class="proj-val">{issues_label}</span><span class="proj-key">Issues</span></div>
    </div>
  </a>""")

    # Comparison chart: real projects sorted by number, unlinked appended at the end
    real_projects = sorted(
        [p for p in projects if not p.get("is_unlinked")],
        key=lambda x: x.get("number") or 0
    )
    unlinked_projects = [p for p in projects if p.get("is_unlinked")]
    chart_data = _json.dumps([
        {
            "period": f"#{p['number']}" if not p.get("is_unlinked") else "∅",
            "label":  p.get("title", f"#{p['number']}"),
            "cost":   p.get("total_cost") or 0.0,
            "sessions": p.get("total_sessions") or 0,
            "unlinked": p.get("is_unlinked", False),
        }
        for p in real_projects + unlinked_projects
    ])

    # ── Index pie data ──────────────────────────────────────────────────────
    # Determine colour for a model display name
    _MC = {"claude": "#58a6ff", "gpt": "#3fb950", "gemini": "#d29922"}
    # Claude model display-name prefixes (after _short_model_name strips "claude-")
    _CLAUDE_DISPLAY = ("sonnet", "haiku", "opus")

    def _idx_model_color(mid_or_display: str) -> str:
        lo = mid_or_display.lower()
        for pfx, col in _MC.items():
            if lo.startswith(pfx):
                return col
        if lo.startswith(_CLAUDE_DISPLAY):
            return "#58a6ff"
        return "#8b949e"

    # Pie 1: Models by Project — fractional weight across top_models per project
    _proj_model_counts: dict[str, float] = {}
    for p in projects:
        _tms = p.get("top_models") or ([{"name": p["top_model"], "share": 100}] if p.get("top_model") else [])
        _total_share = sum(m["share"] for m in _tms) or 1
        for m in _tms:
            if m.get("name"):
                _proj_model_counts[m["name"]] = _proj_model_counts.get(m["name"], 0.0) + m["share"] / _total_share
    idx_pie_by_project_json = _json.dumps(
        sorted([
            {"label": k, "value": round(v, 2), "color": _idx_model_color(k)}
            for k, v in _proj_model_counts.items()
        ], key=lambda x: -x["value"])
    )

    # Pie 2: Models by Cost — global model cost attribution (passed in or empty)
    idx_pie_by_cost_json = _json.dumps(global_model_cost or [])

    # Monthly totals
    monthly_data_json = _json.dumps(global_monthly_data or [])

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Copilot Cost — Projects Overview</title>
<style>
  :root {{
    --bg: #0a0e14; --surface: #111820; --surface2: #161d27;
    --border: #1f2d3d; --text: #cdd9e5; --muted: #636e7b;
    --accent: #58a6ff; --accent2: #79c0ff; --green: #3fb950;
    --yellow: #d29922; --red: #f85149; --unknown: #636e7b;
  }}
  [data-theme="light"] {{
    --bg: #f6f8fa; --surface: #ffffff; --surface2: #f0f2f4;
    --border: #d0d7de; --text: #1f2328; --muted: #656d76;
    --accent: #0969da; --accent2: #218bff; --green: #1a7f37;
    --yellow: #9a6700; --red: #d1242f; --unknown: #656d76;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; padding: 24px; max-width: 1400px; margin: 0 auto; }}

  .header {{ display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }}
  h1 {{ font-size: 22px; font-weight: 700; color: var(--accent);
    text-shadow: 0 0 20px rgba(88,166,255,0.4); letter-spacing: -0.3px; }}
  .meta {{ color: var(--muted); margin-bottom: 24px; font-size: 12px; }}
  .theme-btn {{ background: none; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); cursor: pointer; font-size: 16px; padding: 4px 10px;
    transition: border-color 0.2s, color 0.2s; }}
  .theme-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

  /* Summary stats */
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px; margin-bottom: 32px; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; position: relative; overflow: hidden; }}
  .stat::before {{ content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6; }}
  .stat-value {{ font-size: 28px; font-weight: 700; color: var(--accent);
    font-variant-numeric: tabular-nums; }}
  .stat-label {{ font-size: 11px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.08em; }}

  /* Project cards */
  .proj-grid {{ display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px; margin-bottom: 32px; }}
  .proj-card {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; text-decoration: none; color: var(--text);
    transition: border-color 0.2s, box-shadow 0.2s; display: block; }}
  .proj-card:hover {{ border-color: var(--accent);
    box-shadow: 0 0 20px rgba(88,166,255,0.12); }}
  .proj-card-unlinked {{ border-color: rgba(210,153,34,0.35); }}
  .proj-card-unlinked:hover {{ border-color: var(--yellow);
    box-shadow: 0 0 20px rgba(210,153,34,0.12); }}
  .proj-card-unlinked .proj-num {{ color: var(--yellow); }}
  .proj-card-unlinked .proj-title {{ color: var(--yellow); opacity: 0.85; }}
  .proj-num {{ font-size: 11px; color: var(--muted); font-family: ui-monospace,monospace;
    margin-bottom: 4px; }}
  .proj-title {{ font-size: 15px; font-weight: 600; color: var(--accent2);
    margin-bottom: 16px; line-height: 1.3; }}
  .proj-stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
  .proj-stat {{ display: flex; flex-direction: column; gap: 2px; }}
  .proj-val {{ font-size: 18px; font-weight: 600; color: var(--text);
    font-variant-numeric: tabular-nums; }}
  .proj-key {{ font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.07em; }}

  /* Comparison chart */
  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 13px; font-weight: 600; color: var(--muted); margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.1em; }}
  .chart-panel {{ background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; }}
  .combo-chart {{ width: 100%; min-height: 200px; }}
  .combo-chart svg {{ display: block; width: 100%; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }}
  @media (max-width: 700px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  .chart-panel h3 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 12px; }}
  .pie-chart-wrap {{ width: 100%; min-height: 200px; }}
  .pie-chart-wrap svg {{ display: block; width: 100%; }}
</style>
</head>
<body>

<div class="header">
  <div><h1>⬡ Copilot Cost — Projects Overview</h1></div>
  <button class="theme-btn" id="theme-btn" title="Toggle theme">☀</button>
</div>
<p class="meta">Generated {esc(now_utc)} &nbsp;·&nbsp; {esc(str(len(projects)))} project(s)</p>

<div class="stats">
  <div class="stat">
    <div class="stat-value" data-count="{len(projects)}">{len(projects)}</div>
    <div class="stat-label">Projects</div>
  </div>
  <div class="stat">
    <div class="stat-value" data-count="{total_sessions_all}">{total_sessions_all}</div>
    <div class="stat-label">Total Sessions</div>
  </div>
  <div class="stat">
    <div class="stat-value">{esc(fmt_cost(total_cost_all))}</div>
    <div class="stat-label">Total Est. Cost</div>
  </div>
</div>

<section>
  <h2>Cost &amp; Sessions by Project</h2>
  <div class="chart-panel">
    <div id="chart-compare" class="combo-chart"></div>
  </div>
</section>

<section>
  <h2>Total Cost &amp; Sessions by Month</h2>
  <div class="chart-panel">
    <div id="chart-monthly-total" class="combo-chart"></div>
  </div>
</section>

<div class="two-col">
  <div class="chart-panel">
    <h3>Models by Project (top model per project)</h3>
    <div id="idx-pie-by-project" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Cost (session cost attributed to top model)</h3>
    <div id="idx-pie-by-cost" class="pie-chart-wrap"></div>
  </div>
</div>

<div class="proj-grid">
  {''.join(cards_html)}
</div>

<script>
  // ── Theme ────────────────────────────────────────────────────────────────
  (function initTheme() {{
    const stored = localStorage.getItem('copilot-cost-theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = stored || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '🌙';
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {{
      if (!localStorage.getItem('copilot-cost-theme')) {{
        const t = e.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', t);
        if (btn) btn.textContent = t === 'dark' ? '☀' : '🌙';
        if (typeof redrawAll === 'function') redrawAll();
      }}
    }});
    if (btn) btn.addEventListener('click', () => {{
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('copilot-cost-theme', next);
      btn.textContent = next === 'dark' ? '☀' : '🌙';
      if (typeof redrawAll === 'function') redrawAll();
    }});
  }})();

  function getColors() {{
    const s = getComputedStyle(document.documentElement);
    const g = n => s.getPropertyValue(n).trim();
    return {{
      bg: g('--bg'), surface: g('--surface'), surface2: g('--surface2'),
      border: g('--border'), text: g('--text'), muted: g('--muted'),
      accent: g('--accent'), accent2: g('--accent2'), green: g('--green'),
      yellow: g('--yellow'), red: g('--red'),
    }};
  }}

  function hexAlpha(hex, a) {{
    const r = parseInt(hex.slice(1,3),16), g2 = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return `rgba(${{r}},${{g2}},${{b}},${{a}})`;
  }}


  // Animated counters
  document.querySelectorAll('[data-count]').forEach(el => {{
    const target = parseInt(el.dataset.count, 10);
    if (!target) return;
    const steps = 40, dur = 800;
    let cur = 0;
    const t = setInterval(() => {{
      cur = Math.min(cur + Math.ceil(target / steps), target);
      el.textContent = cur.toLocaleString();
      if (cur >= target) clearInterval(t);
    }}, dur / steps);
  }});

  // Comparison combo chart
  const DATA = {chart_data};

  function drawCompare() {{    const C = getColors();
    const container = document.getElementById('chart-compare');
    if (!container || !DATA.length) return;
    const W = container.clientWidth || 800;
    const H = 200;
    const PAD = {{ t: 24, b: 48, l: 56, r: 52 }};
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = DATA.length;

    const maxCost = Math.max(...DATA.map(d => d.cost), 0.001);
    const maxSess = Math.max(...DATA.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(6, slotW * 0.45);
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {{
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${{PAD.l}}" y1="${{gy.toFixed(1)}}" x2="${{PAD.l+cW}}" y2="${{gy.toFixed(1)}}"
              stroke="${{C.border}}" stroke-width="1" ${{frac < 1 ? 'stroke-dasharray="3,3"' : ''}}/>`;
      s += `<text x="${{PAD.l-6}}" y="${{(gy+4).toFixed(1)}}" text-anchor="end"
              fill="${{C.accent}}" font-size="10">$${{(maxCost*frac).toFixed(2)}}</text>`;
      s += `<text x="${{PAD.l+cW+6}}" y="${{(gy+4).toFixed(1)}}"
              fill="${{C.green}}" font-size="10">${{Math.round(maxSess*frac)}}</text>`;
    }}
    s += `<line x1="${{PAD.l}}" y1="${{PAD.t}}" x2="${{PAD.l}}" y2="${{PAD.t+cH}}" stroke="${{C.border}}" stroke-width="1"/>`;

    DATA.forEach((d, i) => {{
      const bh = Math.max(2, d.cost / maxCost * cH);
      const bx = cx(i) - barW / 2;
      const fill = d.unlinked ? hexAlpha(C.yellow, 0.55) : hexAlpha(C.accent, 0.65);
      s += `<rect x="${{bx.toFixed(1)}}" y="${{(PAD.t+cH-bh).toFixed(1)}}"
              width="${{barW.toFixed(1)}}" height="${{bh.toFixed(1)}}"
              rx="2" fill="${{fill}}"/>`;
    }});

    const pts = DATA.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {{
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {{
        const slope = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = slope; m[i+1] = slope;
      }}
      for (let i = 1; i < pts.length-1; i++) {{
        m[i] = ((pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]) +
                (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0])) / 2;
      }}
      let path = `M ${{pts[0][0].toFixed(1)}} ${{pts[0][1].toFixed(1)}}`;
      for (let i = 0; i < pts.length-1; i++) {{
        const dx = (pts[i+1][0]-pts[i][0])/3;
        path += ` C ${{(pts[i][0]+dx).toFixed(1)}} ${{(pts[i][1]+m[i]*dx).toFixed(1)}} ${{(pts[i+1][0]-dx).toFixed(1)}} ${{(pts[i+1][1]-m[i+1]*dx).toFixed(1)}} ${{pts[i+1][0].toFixed(1)}} ${{pts[i+1][1].toFixed(1)}}`;
      }}
      const fill = path + ` L ${{pts[pts.length-1][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} L ${{pts[0][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} Z`;
      s += `<path d="${{fill}}" fill="${{hexAlpha(C.green, 0.08)}}"/>`;
      s += `<path d="${{path}}" fill="none" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
      pts.forEach(([px,py]) => s += `<circle cx="${{px.toFixed(1)}}" cy="${{py.toFixed(1)}}" r="3" fill="${{C.green}}"/>`);
    }}

    DATA.forEach((d, i) => {{
      const x = cx(i);
      s += `<text x="${{x.toFixed(1)}}" y="${{H-28}}" text-anchor="middle" fill="${{C.muted}}" font-size="11">${{d.period}}</text>`;
      const label = d.label.length > 18 ? d.label.slice(0,16)+'…' : d.label;
      s += `<text x="${{x.toFixed(1)}}" y="${{H-10}}" text-anchor="middle" fill="${{C.muted}}" font-size="9">${{label}}</text>`;
    }});

    s += `<rect x="${{PAD.l}}" y="6" width="10" height="10" rx="2" fill="${{hexAlpha(C.accent, 0.65)}}"/>`;
    s += `<text x="${{PAD.l+14}}" y="15" fill="${{C.accent}}" font-size="10">Cost USD</text>`;
    s += `<line x1="${{PAD.l+72}}" y1="11" x2="${{PAD.l+88}}" y2="11" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
    s += `<circle cx="${{PAD.l+80}}" cy="11" r="3" fill="${{C.green}}"/>`;
    s += `<text x="${{PAD.l+94}}" y="15" fill="${{C.green}}" font-size="10">Sessions</text>`;

    s += '</svg>';
    container.innerHTML = s;
  }}

  // ── Pie charts (models by project / by cost) ───────────────────────────
  const IDX_PIE_BY_PROJECT = {idx_pie_by_project_json};
  const IDX_PIE_BY_COST    = {idx_pie_by_cost_json};

  function drawIdxPie(containerId, data, fmtVal) {{
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) {{
      container.innerHTML = `<p style="color:${{C.muted}};font-size:12px;padding:16px">No data</p>`;
      return;
    }}
    const W = container.clientWidth || 500;
    const H = 200;
    const R = Math.min(H * 0.42, 78);
    const IR = R * 0.55;
    const CX = R + 16, CY = H / 2;
    const LEGEND_X = CX + R + 22;
    const total = data.reduce((s, d) => s + d.value, 0);
    if (total === 0) {{ container.innerHTML = `<p style="color:${{C.muted}};font-size:12px;padding:16px">No data</p>`; return; }}
    const fmt = fmtVal || (v => v.toFixed(2));

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    let startAngle = -Math.PI / 2;
    const segments = data.map(d => {{
      const sweep = (d.value / total) * 2 * Math.PI;
      const seg = {{ ...d, startAngle, sweep }};
      startAngle += sweep;
      return seg;
    }});

    function polarXY(angle, r) {{ return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)]; }}

    segments.forEach(seg => {{
      if (seg.sweep < 0.001) return;
      const [x1o, y1o] = polarXY(seg.startAngle, R);
      const [x2o, y2o] = polarXY(seg.startAngle + seg.sweep, R);
      const [x1i, y1i] = polarXY(seg.startAngle + seg.sweep, IR);
      const [x2i, y2i] = polarXY(seg.startAngle, IR);
      const large = seg.sweep > Math.PI ? 1 : 0;
      const path = `M ${{x1o.toFixed(2)}} ${{y1o.toFixed(2)}} A ${{R}} ${{R}} 0 ${{large}} 1 ${{x2o.toFixed(2)}} ${{y2o.toFixed(2)}} L ${{x1i.toFixed(2)}} ${{y1i.toFixed(2)}} A ${{IR}} ${{IR}} 0 ${{large}} 0 ${{x2i.toFixed(2)}} ${{y2i.toFixed(2)}} Z`;
      s += `<path d="${{path}}" fill="${{seg.color}}" opacity="0.85"><title>${{seg.label}}: ${{fmt(seg.value)}} (${{Math.round(seg.value/total*100)}}%)</title></path>`;
    }});

    s += `<text x="${{CX}}" y="${{CY - 6}}" text-anchor="middle" fill="${{C.text}}" font-size="14" font-weight="700">${{fmt(total)}}</text>`;
    s += `<text x="${{CX}}" y="${{CY + 10}}" text-anchor="middle" fill="${{C.muted}}" font-size="9">total</text>`;

    const legendItemH = 18;
    const legendStartY = CY - (data.length * legendItemH) / 2;
    data.forEach((d, i) => {{
      const ly = legendStartY + i * legendItemH + 8;
      if (ly > H - 4) return;
      const pct = Math.round(d.value / total * 100);
      s += `<rect x="${{LEGEND_X}}" y="${{ly - 8}}" width="10" height="10" rx="2" fill="${{d.color}}" opacity="0.85"/>`;
      s += `<text x="${{LEGEND_X + 14}}" y="${{ly}}" fill="${{C.text}}" font-size="11">${{d.label}}</text>`;
      s += `<text x="${{W - 4}}" y="${{ly}}" text-anchor="end" fill="${{C.muted}}" font-size="10">${{pct}}%</text>`;
    }});
    s += '</svg>';
    container.innerHTML = s;
  }}

  function fmtIdxCost(v) {{ return '$' + v.toFixed(2); }}

  // ── Monthly totals chart ──────────────────────────────────────────────────
  const MONTHLY_DATA = {monthly_data_json};

  function drawMonthly() {{
    const C = getColors();
    const container = document.getElementById('chart-monthly-total');
    if (!container || !MONTHLY_DATA.length) return;
    const W = container.clientWidth || 800;
    const H = 220;
    const PAD = {{ t: 24, b: 52, l: 56, r: 52 }};
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = MONTHLY_DATA.length;

    const maxCost = Math.max(...MONTHLY_DATA.map(d => d.cost), 0.001);
    const maxSess = Math.max(...MONTHLY_DATA.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, Math.min(slotW * 0.55, 40));
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);

    let s = `<svg viewBox="0 0 ${{W}} ${{H}}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {{
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${{PAD.l}}" y1="${{gy.toFixed(1)}}" x2="${{PAD.l+cW}}" y2="${{gy.toFixed(1)}}"
              stroke="${{C.border}}" stroke-width="1" ${{frac < 1 ? 'stroke-dasharray="3,3"' : ''}}/>`;
      s += `<text x="${{PAD.l-6}}" y="${{(gy+4).toFixed(1)}}" text-anchor="end"
              fill="${{C.accent}}" font-size="10">$${{(maxCost*frac).toFixed(2)}}</text>`;
      s += `<text x="${{PAD.l+cW+6}}" y="${{(gy+4).toFixed(1)}}"
              fill="${{C.green}}" font-size="10">${{Math.round(maxSess*frac)}}</text>`;
    }}
    s += `<line x1="${{PAD.l}}" y1="${{PAD.t}}" x2="${{PAD.l}}" y2="${{PAD.t+cH}}" stroke="${{C.border}}" stroke-width="1"/>`;

    MONTHLY_DATA.forEach((d, i) => {{
      const bx = cx(i) - barW / 2;
      const linkedCost = d.cost - d.unlinked_cost;
      const linkedH = Math.max(0, linkedCost / maxCost * cH);
      const unlinkedH = Math.max(0, d.unlinked_cost / maxCost * cH);
      const totalH = Math.max(2, linkedH + unlinkedH);
      const baseY = PAD.t + cH;
      // Linked portion (bottom, blue)
      if (linkedH > 0) {{
        s += `<rect x="${{bx.toFixed(1)}}" y="${{(baseY - linkedH).toFixed(1)}}"
                width="${{barW.toFixed(1)}}" height="${{linkedH.toFixed(1)}}"
                rx="2" fill="${{hexAlpha(C.accent, 0.70)}}">
                <title>${{d.period}}: $${{linkedCost.toFixed(2)}} linked</title></rect>`;
      }}
      // Unlinked portion (top, yellow)
      if (unlinkedH > 0) {{
        s += `<rect x="${{bx.toFixed(1)}}" y="${{(baseY - linkedH - unlinkedH).toFixed(1)}}"
                width="${{barW.toFixed(1)}}" height="${{unlinkedH.toFixed(1)}}"
                rx="2" fill="${{hexAlpha(C.yellow, 0.65)}}">
                <title>${{d.period}}: $${{d.unlinked_cost.toFixed(2)}} unlinked</title></rect>`;
      }}
      // Fallback bar if both are 0 but total > 0
      if (linkedH === 0 && unlinkedH === 0 && d.cost > 0) {{
        s += `<rect x="${{bx.toFixed(1)}}" y="${{(baseY - 2).toFixed(1)}}"
                width="${{barW.toFixed(1)}}" height="2" rx="1"
                fill="${{hexAlpha(C.accent, 0.70)}}"/>`;
      }}
    }});

    const pts = MONTHLY_DATA.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {{
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {{
        const slope = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = slope; m[i+1] = slope;
      }}
      for (let i = 1; i < pts.length-1; i++) {{
        m[i] = ((pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]) +
                (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0])) / 2;
      }}
      let path = `M ${{pts[0][0].toFixed(1)}} ${{pts[0][1].toFixed(1)}}`;
      for (let i = 0; i < pts.length-1; i++) {{
        const dx = (pts[i+1][0]-pts[i][0])/3;
        path += ` C ${{(pts[i][0]+dx).toFixed(1)}} ${{(pts[i][1]+m[i]*dx).toFixed(1)}} ${{(pts[i+1][0]-dx).toFixed(1)}} ${{(pts[i+1][1]-m[i+1]*dx).toFixed(1)}} ${{pts[i+1][0].toFixed(1)}} ${{pts[i+1][1].toFixed(1)}}`;
      }}
      const fillPath = path + ` L ${{pts[pts.length-1][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} L ${{pts[0][0].toFixed(1)}} ${{(PAD.t+cH).toFixed(1)}} Z`;
      s += `<path d="${{fillPath}}" fill="${{hexAlpha(C.green, 0.08)}}"/>`;
      s += `<path d="${{path}}" fill="none" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
      pts.forEach(([px,py], i) => s += `<circle cx="${{px.toFixed(1)}}" cy="${{py.toFixed(1)}}" r="3" fill="${{C.green}}"><title>${{MONTHLY_DATA[i].period}}: ${{MONTHLY_DATA[i].sessions}} sessions</title></circle>`);
    }}

    // X-axis labels — show every month, rotate if crowded
    const rotate = n > 12;
    MONTHLY_DATA.forEach((d, i) => {{
      const x = cx(i);
      const label = d.period.slice(0, 7); // YYYY-MM
      if (rotate) {{
        s += `<text x="${{x.toFixed(1)}}" y="${{H - 4}}" text-anchor="end"
                transform="rotate(-40,${{x.toFixed(1)}},${{H-4}})"
                fill="${{C.muted}}" font-size="9">${{label}}</text>`;
      }} else {{
        s += `<text x="${{x.toFixed(1)}}" y="${{H - 8}}" text-anchor="middle"
                fill="${{C.muted}}" font-size="10">${{label}}</text>`;
      }}
    }});

    s += `<rect x="${{PAD.l}}" y="6" width="10" height="10" rx="2" fill="${{hexAlpha(C.accent, 0.70)}}"/>`;
    s += `<text x="${{PAD.l+14}}" y="15" fill="${{C.accent}}" font-size="10">Linked cost</text>`;
    s += `<rect x="${{PAD.l+88}}" y="6" width="10" height="10" rx="2" fill="${{hexAlpha(C.yellow, 0.65)}}"/>`;
    s += `<text x="${{PAD.l+102}}" y="15" fill="${{C.yellow}}" font-size="10">Unlinked cost</text>`;
    s += `<line x1="${{PAD.l+182}}" y1="11" x2="${{PAD.l+198}}" y2="11" stroke="${{hexAlpha(C.green, 0.8)}}" stroke-width="2"/>`;
    s += `<circle cx="${{PAD.l+190}}" cy="11" r="3" fill="${{C.green}}"/>`;
    s += `<text x="${{PAD.l+204}}" y="15" fill="${{C.green}}" font-size="10">Sessions</text>`;

    s += '</svg>';
    container.innerHTML = s;
  }}

  function redrawAll() {{
    drawCompare();
    drawMonthly();
    drawIdxPie('idx-pie-by-project', IDX_PIE_BY_PROJECT, null);
    drawIdxPie('idx-pie-by-cost',    IDX_PIE_BY_COST,    fmtIdxCost);
  }}
  redrawAll();
  window.addEventListener('resize', redrawAll);
</script>
</body>
</html>"""



def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cost-report.py",
        description="Issue-grouped cost report from merged JSONL logs.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "html"],
        default="text",
        help="Output format: text (default) or html",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help=(
            "Output path. Defaults to stdout for text, "
            f"{DEFAULT_HTML_OUTPUT} for html. Use - for stdout."
        ),
    )
    parser.add_argument(
        "--live",
        metavar="PATH",
        default=str(LIVE_JSONL),
        help=f"Live cost log (default: {LIVE_JSONL})",
    )
    parser.add_argument(
        "--historical",
        metavar="PATH",
        default=str(HISTORICAL_JSONL),
        help=f"Historical cost log (default: {HISTORICAL_JSONL})",
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        help="GitHub repository (e.g. org/custom-repo-linux) for project filtering",
    )
    parser.add_argument(
        "--project",
        metavar="N",
        type=int,
        help="GitHub project number — show only sessions linked to issues in this project",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Generate one dashboard per project found in the repo",
    )
    parser.add_argument(
        "--project-owner",
        metavar="ORG",
        help="Org or user that owns the GitHub projects (defaults to the repo owner). "
             "Use when projects live under a different org than data_repo "
             "(e.g. --repo <owner>/copilot-workflow --project-owner custom-repo).",
    )
    parser.add_argument(
        "--gh-host",
        metavar="HOST",
        default=os.environ.get("GH_HOST"),
        help="GitHub Enterprise host (e.g. github.com); falls back to GH_HOST env var",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        help="Include only sessions started on or after this date (YYYY-MM-DD or YYYY-MM for month, YYYY for year)",
    )
    parser.add_argument(
        "--until",
        metavar="DATE",
        help="Include only sessions started on or before this date (YYYY-MM-DD or YYYY-MM for month, YYYY for year)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Workflow config YAML for repo_aliases (auto-detected if omitted)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=".gru/attributions.db",
        help="attributions.db path for DB overlay (default: .gru/attributions.db)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(argv)


def _parse_date_bound(value: str, end_of_period: bool) -> str:
    """
    Expand a partial date string to a full ISO date for comparison.
    'YYYY'    → 'YYYY-01-01' (start) or 'YYYY-12-31' (end)
    'YYYY-MM' → 'YYYY-MM-01' (start) or 'YYYY-MM-{last}' (end)
    'YYYY-MM-DD' → unchanged
    """
    import calendar
    parts = value.split("-")
    if len(parts) == 1:  # year only
        return f"{parts[0]}-12-31" if end_of_period else f"{parts[0]}-01-01"
    if len(parts) == 2:  # year-month
        year, month = int(parts[0]), int(parts[1])
        if end_of_period:
            last = calendar.monthrange(year, month)[1]
            return f"{parts[0]}-{parts[1]:0>2}-{last:02d}"
        return f"{parts[0]}-{parts[1]:0>2}-01"
    return value  # already YYYY-MM-DD


def filter_by_date(records: list[dict], since: Optional[str], until: Optional[str]) -> list[dict]:
    """Filter records to those whose started_at falls within [since, until] (inclusive)."""
    if not since and not until:
        return records
    since_bound = _parse_date_bound(since, end_of_period=False) if since else None
    until_bound = _parse_date_bound(until, end_of_period=True) if until else None
    filtered = []
    skipped = 0
    for rec in records:
        date = (rec.get("started_at") or "")[:10]
        if not date:
            # sessions with no timestamp are always included (unknown period)
            filtered.append(rec)
            continue
        if since_bound and date < since_bound:
            skipped += 1
            continue
        if until_bound and date > until_bound:
            skipped += 1
            continue
        filtered.append(rec)
    if skipped:
        log.info("Date filter [%s → %s]: %d/%d session(s) kept",
                 since_bound or "…", until_bound or "…", len(filtered), len(filtered) + skipped)
    return filtered


def _write_dashboard(args, records: list[dict], aggregated: dict[str, dict],
                     title: str = "", out_path_override: Optional[str] = None) -> None:
    if args.format == "html":
        content = render_html(records, aggregated, title=title)
        out_path = out_path_override or args.output or str(DEFAULT_HTML_OUTPUT)
        if out_path == "-":
            sys.stdout.write(content)
        else:
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            log.info("HTML dashboard written: %s", p)
    else:
        content = render_text(records, aggregated, title=title)
        out_path = out_path_override or args.output
        if out_path and out_path != "-":
            Path(out_path).write_text(content, encoding="utf-8")
            log.info("Text report written: %s", out_path)
        else:
            sys.stdout.write(content + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    records = load_records(Path(args.live), Path(args.historical))
    records = filter_by_date(records, args.since, args.until)

    # Overlay attributions from DB (single source of truth — never mutates JSONL)
    db_path = Path(getattr(args, "db", "") or ".gru/attributions.db")
    records = apply_db_attributions(records, db_path)

    # Load repo aliases from config (auto-detected if not given)
    config_path = getattr(args, "config", None)
    if not config_path:
        for candidate in [".gru/config.yml", ".gru/config.yaml"]:
            if Path(candidate).exists():
                config_path = candidate
                break
    aliases = load_repo_aliases(config_path)
    if aliases:
        log.debug("Loaded %d repo aliases from %s", len(aliases), config_path)
    repo_projects = load_repo_projects(config_path)
    if repo_projects:
        log.debug("Loaded %d repo→project mappings from %s", len(repo_projects), config_path)

    # Build a period label to append to titles when date filters are active
    period_label = ""
    if args.since or args.until:
        parts = []
        if args.since:
            parts.append(f"from {args.since}")
        if args.until:
            parts.append(f"to {args.until}")
        period_label = " · " + " ".join(parts)

    if args.all_projects:
        if not args.repo:
            log.error("--all-projects requires --repo")
            return 1
        proj_owner_repo = args.repo
        if args.project_owner:
            proj_owner_repo = f"{args.project_owner}/{args.repo.split('/')[1]}"
        projects = fetch_all_projects(proj_owner_repo, args.gh_host)
        if not projects:
            log.warning("No projects found for repo %s", proj_owner_repo)
            return 1
        out_dir = Path(args.output) if args.output and args.output != "-" else DEFAULT_HTML_OUTPUT.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        projects_summary = []
        all_known_issues: set[tuple[str, int]] = set()
        all_hinted_sessions: set[str] = set()
        for proj in projects:
            num = proj["number"]
            title_str = proj.get("title", f"Project {num}")
            allowed = fetch_project_issues(proj_owner_repo, num, args.gh_host)
            if allowed is None:
                log.warning("Skipping project #%d: could not fetch issues", num)
                continue
            allowed = expand_allowed_with_aliases(allowed, aliases)
            all_known_issues.update(allowed)
            # Sessions explicitly hinted to this project (issue=-1, project_hint=num)
            hinted = {r.get("session_id") for r in records if r.get("project_hint") == num and r.get("session_id")}
            # Sessions with no issue_refs whose repo maps to this project
            hinted |= repo_project_hinted_sessions(records, num, repo_projects, aliases)
            all_hinted_sessions.update(hinted)
            agg = aggregate_by_issue(records, allowed_issues=allowed, hinted_sessions=hinted, project_num=num)
            # Collect summary stats for index page (exclude _unlinked_summary metadata key)
            agg_data = [v for k, v in agg.items() if not k.startswith("_")]
            total_cost = sum(v.get("est_cost_usd") or 0.0 for v in agg_data)
            total_sess = sum(v.get("sessions") or 0 for v in agg_data)
            total_prem = sum(v.get("premium_reqs") or 0 for v in agg_data)
            # Skip projects with no issues and no sessions (empty / deleted projects)
            if not allowed and total_sess == 0:
                log.info("Skipping empty project #%d '%s'", num, title_str)
                continue
            # Narrow records to only sessions that belong to this project
            proj_records = [
                r for r in records
                if any(
                    (r.get("repository") or "", ref["issue"]) in allowed
                    for ref in (r.get("issue_refs") or [])
                )
                or r.get("project_hint") == num
            ]
            dashboard_filename = f"project-{num}.html"
            dashboard_path = out_dir / dashboard_filename
            if args.format == "html":
                content = render_html(proj_records, agg, title=f"{title_str} (#{num}){period_label}")
                dashboard_path.write_text(content, encoding="utf-8")
                log.info("HTML dashboard written: %s", dashboard_path)
            # Determine top 3 models for this project from project-scoped records
            proj_model_rows = aggregate_by_model(proj_records)
            # compute total requests across all models for share %
            _proj_total_reqs = sum(row["requests_count"] for row in proj_model_rows) or 1
            proj_top_models = [
                {"name": row["display_name"],
                 "share": int(row["requests_count"] / _proj_total_reqs * 100)}
                for row in proj_model_rows[:3]
            ]
            projects_summary.append({
                "number": num,
                "title": title_str,
                "issues_count": len(agg_data),
                "total_sessions": total_sess,
                "total_premium": total_prem,
                "total_cost": total_cost,
                "dashboard_url": dashboard_filename,
                "top_model":  proj_top_models[0]["name"] if proj_top_models else None,
                "top_models": proj_top_models,
            })

        # Build a virtual "Unlinked" project for sessions not attributed to any known project
        all_hinted_project_nums = {p["number"] for p in projects if p.get("number")}
        unlinked_records = [
            r for r in records
            if not any(
                (r.get("repository") or "", ref["issue"]) in all_known_issues
                for ref in (r.get("issue_refs") or [])
            )
            and r.get("session_id") not in all_hinted_sessions
            and r.get("project_hint") not in all_hinted_project_nums
        ]
        if unlinked_records:
            unlinked_agg = aggregate_by_issue(unlinked_records)
            unlinked_dashboard = "project-unlinked.html"
            unlinked_path = out_dir / unlinked_dashboard
            if args.format == "html":
                content = render_html(
                    unlinked_records, unlinked_agg,
                    title=f"Unlinked Sessions{period_label}"
                )
                unlinked_path.write_text(content, encoding="utf-8")
                log.info("HTML dashboard written: %s", unlinked_path)
            ul_data = [v for k, v in unlinked_agg.items() if not k.startswith("_")]
            ul_cost = sum(v.get("est_cost_usd") or 0.0 for v in ul_data)
            ul_sess = sum(v.get("sessions") or 0 for v in ul_data)
            ul_prem = sum(v.get("premium_reqs") or 0 for v in ul_data)
            ul_model_rows = aggregate_by_model(unlinked_records)
            _ul_total_reqs = sum(row["requests_count"] for row in ul_model_rows) or 1
            ul_top_models = [
                {"name": row["display_name"],
                 "share": int(row["requests_count"] / _ul_total_reqs * 100)}
                for row in ul_model_rows[:3]
            ]
            projects_summary.append({
                "number": "∅",
                "title": "Unlinked Sessions",
                "issues_count": 0,
                "total_sessions": ul_sess,
                "total_premium": ul_prem,
                "total_cost": ul_cost,
                "dashboard_url": unlinked_dashboard,
                "top_model":  ul_top_models[0]["name"] if ul_top_models else None,
                "top_models": ul_top_models,
                "is_unlinked": True,
            })
        if args.format == "html" and projects_summary:
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            # Global model data from all records
            global_model_rows = aggregate_by_model(records)
            # Global model cost: split each session's cost proportionally across top 3 models
            _MC_COLORS = {"claude": "#58a6ff", "gpt": "#3fb950", "gemini": "#d29922"}
            def _g_model_color(mid: str) -> str:
                lo = mid.lower()
                for pfx, col in _MC_COLORS.items():
                    if lo.startswith(pfx):
                        return col
                return "#8b949e"
            _gcost: dict[str, float] = {}
            for r in records:
                mm = r.get("model_metrics") or {}
                c = r.get("est_cost_usd") or 0.0
                if not mm or c == 0.0:
                    continue
                _total_reqs = sum((mm[m].get("requests_count") or 0) for m in mm)
                if _total_reqs == 0:
                    continue
                for _mid in sorted(mm, key=lambda m: -(mm[m].get("requests_count") or 0))[:3]:
                    _reqs = mm[_mid].get("requests_count") or 0
                    if _reqs > 0:
                        _gcost[_mid] = _gcost.get(_mid, 0.0) + c * _reqs / _total_reqs
            global_model_cost = sorted([
                {"label": _short_model_name(m), "value": round(v, 4), "color": _g_model_color(m)}
                for m, v in _gcost.items() if v > 0
            ], key=lambda x: -x["value"])
            # Monthly totals across all records, split by linked vs unlinked
            _unlinked_ids = {r.get("session_id") for r in unlinked_records}
            _monthly: dict[str, dict] = {}
            for r in records:
                ts = r.get("started_at") or r.get("updated_at") or ""
                if len(ts) >= 7:
                    month = ts[:7]  # "YYYY-MM"
                    m = _monthly.setdefault(month, {"cost": 0.0, "sessions": 0, "unlinked_cost": 0.0, "unlinked_sessions": 0})
                    c = r.get("est_cost_usd") or 0.0
                    m["cost"] += c
                    m["sessions"] += 1
                    if r.get("session_id") in _unlinked_ids:
                        m["unlinked_cost"] += c
                        m["unlinked_sessions"] += 1
            global_monthly_data = [
                {"period": k, "cost": round(v["cost"], 2), "sessions": v["sessions"],
                 "unlinked_cost": round(v["unlinked_cost"], 2), "unlinked_sessions": v["unlinked_sessions"]}
                for k, v in sorted(_monthly.items())
            ]
            index_html = render_index_html(
                projects_summary, generated_at=now_utc,
                global_model_data=global_model_rows,
                global_model_cost=global_model_cost,
                global_monthly_data=global_monthly_data,
            )
            index_path = out_dir / "index.html"
            index_path.write_text(index_html, encoding="utf-8")
            log.info("Index page written: %s", index_path)
        return 0

    if args.project:
        if not args.repo:
            log.error("--project requires --repo")
            return 1
        allowed = fetch_project_issues(args.repo, args.project, args.gh_host)
        if allowed is None:
            log.error("Could not fetch issues for project #%d", args.project)
            return 1
        allowed = expand_allowed_with_aliases(allowed, aliases)
        hinted = {r.get("session_id") for r in records if r.get("project_hint") == args.project and r.get("session_id")}
        hinted |= repo_project_hinted_sessions(records, args.project, repo_projects, aliases)
        agg = aggregate_by_issue(records, allowed_issues=allowed, hinted_sessions=hinted, project_num=args.project)
        proj_records = [
            r for r in records
            if any(
                (r.get("repository") or "", ref["issue"]) in allowed
                for ref in (r.get("issue_refs") or [])
            )
            or r.get("session_id") in hinted
        ]
        _write_dashboard(args, proj_records, agg, title=f"Project #{args.project}{period_label}")
        return 0

    # Default: full dashboard (no project filter)
    agg = aggregate_by_issue(records)
    _write_dashboard(args, records, agg, title=period_label.strip(" ·") if period_label else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
