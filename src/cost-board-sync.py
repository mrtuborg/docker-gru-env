#!/usr/bin/env python3
"""
cost-board-sync.py — Sync per-issue cost totals from the local JSONL cost log
to a GitHub Projects V2 "Cost ($)" number field.

For each issue on the project board that has cost data in the local JSONL:
  - Reads the current field value
  - Writes the full running total (sum across ALL sessions for that issue)

The field is created automatically on first run if it doesn't exist.
Issues are matched by (repository, issue_number) so boards with issues from
multiple repos are handled correctly.

Usage:
    # Sync a single project board
    python3 src/cost-board-sync.py \\
        --project-owner custom-repo --project N [--gh-host HOST]

    # Sync ALL project boards under an org
    python3 src/cost-board-sync.py \\
        --project-owner custom-repo --all-projects [--gh-host HOST]

    # Sync a single issue after a session (called from watcher-run)
    python3 src/cost-board-sync.py \\
        --project-owner custom-repo --all-projects --issue 42 --repo org/repo

    # Preview without writing
    python3 src/cost-board-sync.py \\
        --project-owner custom-repo --all-projects --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
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
DEFAULT_ATTR_DB  = Path(".gru/attributions.db")
FIELD_NAME       = "Cost ($)"


def _load_attr_db_module():
    here = Path(__file__).parent
    spec = importlib.util.spec_from_file_location("attributions_db", here / "attributions_db.py")
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apply_db_attributions(records: list[dict], db_path: Path) -> list[dict]:
    """Overlay attribution data from attributions.db onto JSONL records.

    Sessions with existing issue_refs are not overwritten unless the DB entry
    has source='manual'.
    """
    if not db_path.exists():
        return records
    try:
        mod  = _load_attr_db_module()
        conn = mod.open_db(db_path)
        db_attrs = {r["session_prefix"]: r for r in mod.query_all(conn)}
        conn.close()
    except Exception as exc:
        log.warning("Could not load attributions.db: %s", exc)
        return records

    result = []
    for rec in records:
        sid    = rec.get("session_id") or ""
        prefix = sid[:8]
        attr   = db_attrs.get(prefix)
        if attr is None:
            result.append(rec)
            continue
        existing_refs = rec.get("issue_refs") or []
        is_manual     = attr.get("source") == "manual"
        if not existing_refs or is_manual:
            rec = dict(rec)
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
        result.append(rec)
    return result


def load_repo_aliases(config_path: Optional[str]) -> dict[str, str]:
    """Load repo_aliases from a workflow config YAML file. Returns {} if unavailable."""
    if not config_path or not _HAVE_YAML:
        return {}
    try:
        with open(config_path) as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("repo_aliases") or {}
        return {k.lower(): v.lower() for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, Exception):
        return {}

log = logging.getLogger("cost-board-sync")


# ---------------------------------------------------------------------------
# gh CLI helpers (self-contained — no imports from other src/ scripts)
# ---------------------------------------------------------------------------

def _gh(args: list[str], gh_host: Optional[str] = None) -> Optional[str]:
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


def _graphql(query: str, variables: dict, gh_host: Optional[str]) -> Optional[dict]:
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-F" if isinstance(v, (int, float)) else "-f", f"{k}={v}"]
    raw = _gh(args, gh_host=gh_host)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# JSONL loader — deduplicates by session_id, live wins over historical
# ---------------------------------------------------------------------------

def load_records(live_path: Path, historical_path: Path) -> list[dict]:
    records: dict[str, dict] = {}
    for path, label in [(historical_path, "historical"), (live_path, "live")]:
        if not path.exists():
            log.debug("%s JSONL not found: %s", label, path)
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
                            records[sid] = rec
                    except json.JSONDecodeError:
                        log.warning("%s line %d: malformed JSON, skipped", path, lineno)
        except OSError as exc:
            log.warning("Cannot read %s: %s", path, exc)
    return list(records.values())


# ---------------------------------------------------------------------------
# Cost aggregation — returns {(repo_lower, issue_number): total_usd}
# ---------------------------------------------------------------------------

def aggregate_costs(records: list[dict], aliases: dict[str, str] | None = None) -> dict[tuple[str, int], float]:
    """Sum est_cost_usd per (repo, issue_number) across all records.

    aliases maps session repo names (lower) to canonical repo names (lower),
    so costs on renamed/forked repos still match the project board items.
    """
    _aliases = aliases or {}
    totals: dict[tuple[str, int], float] = {}
    for rec in records:
        cost = rec.get("est_cost_usd")
        if not cost:
            continue
        rec_repo = (rec.get("repository") or "").lower()
        canonical_repo = _aliases.get(rec_repo, rec_repo)
        for ref in rec.get("issue_refs") or []:
            num = ref.get("issue")
            # Skip non-integer or non-positive issue values (e.g. -1 = personal/untracked)
            if not isinstance(num, int) or num <= 0 or not canonical_repo:
                continue
            key = (canonical_repo, num)
            totals[key] = totals.get(key, 0.0) + cost
    return totals


# ---------------------------------------------------------------------------
# Project board helpers
# ---------------------------------------------------------------------------

def _detect_entity(owner: str, project_num: int, gh_host: Optional[str]) -> Optional[str]:
    for entity in ("organization", "user"):
        data = _graphql(
            f"query($l:String!,$n:Int!){{ {entity}(login:$l){{ projectV2(number:$n){{ id }} }} }}",
            {"l": owner, "n": project_num}, gh_host,
        )
        if data is None:
            continue
        try:
            if data["data"][entity]["projectV2"] is not None:
                return entity
        except (KeyError, TypeError):
            continue
    return None


def fetch_project_meta(owner: str, project_num: int, entity: str, gh_host: Optional[str]) -> Optional[dict]:
    """Return {id, title, fields: {name: {id, dataType}}, items: {(repo_lower, issue_num): {item_id, current_cost, sub_issue_keys}}}."""
    query = f"""
    query($l:String!,$n:Int!) {{
      {entity}(login:$l) {{
        projectV2(number:$n) {{
          id
          title
          fields(first:30) {{ nodes {{
            ... on ProjectV2Field {{ id name dataType }}
          }}}}
          items(first:100) {{ nodes {{
            id
            fieldValues(first:20) {{ nodes {{
              ... on ProjectV2ItemFieldNumberValue {{
                number
                field {{ ... on ProjectV2Field {{ id name }} }}
              }}
            }}}}
            content {{
              ... on Issue {{
                number
                repository {{ nameWithOwner }}
                subIssues(first:50) {{ nodes {{ number repository {{ nameWithOwner }} }} }}
              }}
            }}
          }}}}
        }}
      }}
    }}
    """
    data = _graphql(query, {"l": owner, "n": project_num}, gh_host)
    if data is None:
        return None
    try:
        proj = data["data"][entity]["projectV2"]
    except (KeyError, TypeError):
        return None
    if proj is None:
        return None

    fields = {}
    for f in proj["fields"]["nodes"]:
        if f and f.get("name"):
            fields[f["name"]] = {"id": f["id"], "dataType": f.get("dataType", "")}

    # Key: (repo_lower, issue_num) to handle multi-repo boards correctly
    items: dict[tuple[str, int], dict] = {}
    for node in proj["items"]["nodes"]:
        c = node.get("content")
        if not c or not c.get("number"):
            continue
        repo_name = (c.get("repository") or {}).get("nameWithOwner", "")
        current_cost = None
        for fv in node.get("fieldValues", {}).get("nodes", []):
            if fv and fv.get("field", {}).get("name") == FIELD_NAME:
                current_cost = fv.get("number")
        # Collect sub-issue keys for parent cost aggregation
        sub_issue_keys = []
        for sub in (c.get("subIssues") or {}).get("nodes") or []:
            if not sub or not sub.get("number"):
                continue
            sub_repo = (sub.get("repository") or {}).get("nameWithOwner", repo_name)
            sub_issue_keys.append((sub_repo.lower(), sub["number"]))
        key = (repo_name.lower(), c["number"])
        items[key] = {
            "item_id": node["id"],
            "current_cost": current_cost,
            "repo": repo_name,
            "sub_issue_keys": sub_issue_keys,
        }

    return {"id": proj["id"], "title": proj.get("title", f"#{project_num}"), "fields": fields, "items": items}


def ensure_cost_field(project_id: str, fields: dict, gh_host: Optional[str]) -> Optional[str]:
    """Return the field ID for FIELD_NAME, creating it if necessary."""
    if FIELD_NAME in fields:
        fid = fields[FIELD_NAME]["id"]
        log.debug("Found existing field '%s': %s", FIELD_NAME, fid)
        return fid

    log.info("Creating '%s' NUMBER field on project…", FIELD_NAME)
    data = _graphql(
        """
        mutation($pid:ID!,$name:String!) {
          createProjectV2Field(input:{projectId:$pid, dataType:NUMBER, name:$name}) {
            projectV2Field { ... on ProjectV2Field { id name } }
          }
        }
        """,
        {"pid": project_id, "name": FIELD_NAME},
        gh_host,
    )
    try:
        fid = data["data"]["createProjectV2Field"]["projectV2Field"]["id"]
        log.info("Created field '%s': %s", FIELD_NAME, fid)
        return fid
    except (KeyError, TypeError) as exc:
        log.error("Failed to create field: %s | response: %s", exc, data)
        return None


def update_field_value(
    project_id: str,
    item_id: str,
    field_id: str,
    value: float,
    gh_host: Optional[str],
) -> bool:
    # Embed the float literal directly — gh CLI's -F only accepts integers.
    query = f"""
    mutation {{
      updateProjectV2ItemFieldValue(input:{{
        projectId:"{project_id}", itemId:"{item_id}", fieldId:"{field_id}",
        value:{{number:{value:.4f}}}
      }}) {{ projectV2Item {{ id }} }}
    }}
    """
    data = _graphql(query, {}, gh_host)
    try:
        return data["data"]["updateProjectV2ItemFieldValue"]["projectV2Item"]["id"] is not None
    except (KeyError, TypeError):
        log.error("Update failed for item %s: %s", item_id, data)
        return False


def fetch_all_project_numbers(owner: str, entity: str, gh_host: Optional[str]) -> list[int]:
    """Return all project numbers owned by owner."""
    query = f"""
    query($l:String!) {{
      {entity}(login:$l) {{
        projectsV2(first:50) {{ nodes {{ number title }} }}
      }}
    }}
    """
    data = _graphql(query, {"l": owner}, gh_host)
    try:
        nodes = data["data"][entity]["projectsV2"]["nodes"]
        return [n["number"] for n in nodes if n]
    except (KeyError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Sync one project board
# ---------------------------------------------------------------------------

def sync_project(
    owner: str,
    project_num: int,
    entity: str,
    cost_by_issue: dict[tuple[str, int], float],
    gh_host: Optional[str],
    dry_run: bool,
) -> tuple[int, int]:
    """Sync costs to one project board. Returns (updated, skipped)."""
    meta = fetch_project_meta(owner, project_num, entity, gh_host)
    if meta is None:
        log.error("  Failed to fetch metadata for project #%d", project_num)
        return 0, 0

    if not meta["items"]:
        log.debug("  Project #%d '%s': no issues — skipping", project_num, meta["title"])
        return 0, 0

    log.info("Project #%d '%s':", project_num, meta["title"])

    if dry_run:
        field_id = meta["fields"].get(FIELD_NAME, {}).get("id", "<would-create>")
    else:
        field_id = ensure_cost_field(meta["id"], meta["fields"], gh_host)
        if field_id is None:
            return 0, 0

    # Augment cost_by_issue: parent issues get the sum of their children's costs.
    # Children costs are read from the board's existing field values first (so costs
    # written directly to the board — but not yet in the JSONL log — are included),
    # falling back to cost_by_issue for issues not yet on the board.
    # We make a shallow copy so the original dict is unchanged between projects.
    cost_by_issue = dict(cost_by_issue)

    # Two-pass aggregation so intermediate parents (grandparent→parent→child) are
    # resolved correctly regardless of the order items were returned by GraphQL.
    # Pass 1 uses board current_cost (immutable); pass 2 uses the dict updated in pass 1.
    for _pass in range(2):
        for key, item in meta["items"].items():
            sub_keys = item.get("sub_issue_keys") or []
            if not sub_keys:
                continue
            children_total = 0.0
            for sk in sub_keys:
                child_item = meta["items"].get(sk)
                if child_item is not None:
                    # Prefer board value (pass 1) or already-augmented JSONL value (pass 2).
                    # Explicit None check: board value of 0.0 is valid and must not fall back.
                    board_val = child_item.get("current_cost")
                    children_total += (
                        board_val if board_val is not None else cost_by_issue.get(sk, 0.0)
                    )
                else:
                    children_total += cost_by_issue.get(sk, 0.0)
            if children_total > 0:
                existing = cost_by_issue.get(key, 0.0)
                if _pass == 0:
                    log.debug(
                        "  parent #%d: children sum $%.4f (was $%.4f in cost log)",
                        key[1], children_total, existing,
                    )
                # Parent cost = sum of children (overrides any direct attribution)
                cost_by_issue[key] = children_total

    updated = 0
    skipped = 0
    any_data = False

    for (repo, issue_num), new_total in sorted(cost_by_issue.items(), key=lambda x: (x[0][0], x[0][1])):
        item = meta["items"].get((repo, issue_num))
        if item is None:
            skipped += 1
            continue

        any_data = True
        current = item["current_cost"] or 0.0
        rounded = round(new_total, 4)

        if abs(rounded - current) < 0.0001:
            log.info("  #%-5d  $%8.4f  (unchanged)  %s", issue_num, rounded, repo)
            continue

        log.info("  #%-5d  $%8.4f  (was $%.4f%s)  %s",
                 issue_num, rounded, current,
                 " — DRY RUN" if dry_run else "",
                 repo)

        if not dry_run:
            ok = update_field_value(meta["id"], item["item_id"], field_id, rounded, gh_host)
            if ok:
                updated += 1
            else:
                log.error("    Failed to update #%d", issue_num)
        else:
            updated += 1

    if not any_data:
        log.info("  (no cost data for issues on this board)")

    return updated, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync per-issue cost totals to GitHub Projects V2 'Cost ($)' field.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo",     default=None, metavar="OWNER/REPO",
                        help="Filter to a specific repo's issues (optional; also used to derive --project-owner)")
    proj_group = parser.add_mutually_exclusive_group(required=True)
    proj_group.add_argument("--project",  type=int, metavar="N",
                        help="GitHub project number")
    proj_group.add_argument("--all-projects", action="store_true",
                        help="Sync all projects under --project-owner")
    parser.add_argument("--project-owner", default=None, metavar="ORG",
                        help="Org/user owning the project(s) (default: derived from --repo)")
    parser.add_argument("--config",   default=None, metavar="PATH",
                        help="Workflow config YAML for repo_aliases (default: auto-detect)")
    parser.add_argument("--gh-host",  default=None, metavar="HOST",
                        help="GitHub Enterprise host (falls back to GH_HOST env var)")
    parser.add_argument("--issue",    type=int, default=None, metavar="N",
                        help="Sync only this issue number (default: all issues on board)")
    parser.add_argument("--live",     default=str(LIVE_JSONL), metavar="PATH",
                        help=f"Live cost log (default: {LIVE_JSONL})")
    parser.add_argument("--historical", default=str(HISTORICAL_JSONL), metavar="PATH",
                        help=f"Historical cost log (default: {HISTORICAL_JSONL})")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Print what would be updated, but do not write")
    parser.add_argument("--db",       default=str(DEFAULT_ATTR_DB), metavar="PATH",
                        help=f"attributions.db path for overlay (default: {DEFAULT_ATTR_DB})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    gh_host = args.gh_host or os.environ.get("GH_HOST")

    # Auto-detect config if not given
    config_path = args.config
    if not config_path:
        for candidate in [".gru/config.yml", ".gru/config.yaml"]:
            if Path(candidate).exists():
                config_path = candidate
                break
    aliases = load_repo_aliases(config_path)
    if aliases:
        log.debug("Loaded %d repo aliases from %s", len(aliases), config_path)

    # 1. Derive project owner
    owner = args.project_owner
    if not owner:
        if args.repo:
            owner = args.repo.split("/")[0]
        else:
            log.error("--project-owner (or --repo) is required")
            return 1

    # 2. Detect entity type (org vs user) — use any project number for detection
    # For --all-projects we detect via org query; fall back to user
    entity = None
    if args.all_projects:
        # Just need to know the entity type; try org first
        for etype in ("organization", "user"):
            data = _graphql(
                f"query($l:String!){{ {etype}(login:$l){{ id }} }}",
                {"l": owner}, gh_host,
            )
            if data and data.get("data", {}).get(etype):
                entity = etype
                break
    else:
        entity = _detect_entity(owner, args.project, gh_host)

    if entity is None:
        log.error("Owner '%s' not found (tried organization and user)", owner)
        return 1

    # 3. Load and aggregate costs — keyed by (repo_lower, issue_num)
    records = load_records(Path(args.live), Path(args.historical))
    # Apply attributions.db overlay so DB-only attributions are visible to the board sync
    records = apply_db_attributions(records, Path(getattr(args, "db", None) or DEFAULT_ATTR_DB))
    cost_by_issue = aggregate_costs(records, aliases)

    # Filter to specific repo if given
    if args.repo:
        repo_filter = args.repo.lower()
        cost_by_issue = {k: v for k, v in cost_by_issue.items() if k[0] == repo_filter}

    # Filter to specific issue if given
    if args.issue is not None:
        cost_by_issue = {k: v for k, v in cost_by_issue.items() if k[1] == args.issue}

    if not cost_by_issue:
        log.info("No cost data found%s%s — nothing to sync.",
                 f" for {args.repo}" if args.repo else "",
                 f" issue #{args.issue}" if args.issue else "")
        return 0

    log.info("Cost entries to sync: %d across %d issue(s)",
             len(cost_by_issue),
             len({k[1] for k in cost_by_issue}))

    # 4. Get project numbers to sync
    if args.all_projects:
        project_nums = fetch_all_project_numbers(owner, entity, gh_host)
        if not project_nums:
            log.error("No projects found under '%s'", owner)
            return 1
        log.info("Found %d project(s) under '%s'", len(project_nums), owner)
    else:
        project_nums = [args.project]

    # 5. Sync each project
    total_updated = 0
    total_skipped = 0
    for pnum in sorted(project_nums):
        u, s = sync_project(owner, pnum, entity, cost_by_issue, gh_host, args.dry_run)
        total_updated += u
        total_skipped += s

    log.info("")
    log.info("Total: %d updated, %d issues not on any board%s.",
             total_updated, total_skipped, " (dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
