#!/usr/bin/env python3
"""
Seed the gru-server DB from the HIL stress-test config file.

Usage (inside container):
    python /app/seed.py [--config PATH] [--token TOKEN] [--dry-run]

--config  path to hil-stress/config.yml (default: /work/hil-stress/config.yml)
TOKEN is the GHE personal access token. If omitted, reads GRU_GHE_TOKEN env var.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


DEFAULT_CONFIG = "/work/hil-stress/config.yml"
CONNECTOR_ID   = "ghe-roommate"
PIPELINE_ID    = "hil-stress"


def _load_config(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML not installed — cannot read config.yml")
    with open(path) as f:
        return yaml.safe_load(f)


def _data_dir() -> str:
    return os.environ.get("GRU_DATA_DIR", "/data")


async def _insert_connector(cfg: dict, token: str | None, dry_run: bool):
    sys.path.insert(0, "/app")
    from server.config import init_db, upsert_plugin
    from server.vault import store_secret

    gh_host = cfg.get("gh_host", "github.com")
    project = cfg.get("project", {})
    connector_cfg = {
        "host":           gh_host,
        "project_owner":  project.get("owner", ""),
        "project_number": project.get("number", 0),
    }
    print(f"  connector: {CONNECTOR_ID} ({gh_host}) owner={connector_cfg['project_owner']}")
    if dry_run:
        return

    os.environ.setdefault("GRU_DATA_DIR", _data_dir())
    await init_db()
    await upsert_plugin(CONNECTOR_ID, "github", connector_cfg, enabled=True)
    if token:
        await store_secret(CONNECTOR_ID, "token", token)
        print("  token stored in vault ✓")
    else:
        print("  no token — authorize via UI after start")


async def _insert_pipeline(cfg: dict, dry_run: bool):
    from server.config import upsert_pipeline

    project  = cfg.get("project",  {})
    watcher  = cfg.get("watcher",  {})
    findings = cfg.get("findings_project", {})

    stage_names = watcher.get("stage_order", [])
    stages = []
    for i, name in enumerate(stage_names):
        stages.append({"column_name": name, "actor": "ai", "stage_index": i})
    # final human review stage
    stages.append({"column_name": "Review", "actor": "human", "stage_index": len(stages)})

    models_raw = watcher.get("models", [{"model": "claude-sonnet-4.6", "priority": 1}])

    row = {
        "id":                    PIPELINE_ID,
        "name":                  project.get("name", "HIL Stress Test"),
        "enabled":               0,  # paused until user starts it
        "plugin_id":             CONNECTOR_ID,
        "board_type":            "github",
        "project_owner":         project.get("owner", ""),
        "project_number":        project.get("number", 0),
        "poll_interval":         watcher.get("poll_interval", 300),
        "max_issues":            watcher.get("max_issues", 10),
        "max_retries":           3,
        "session_timeout_hours": watcher.get("session_timeout_hours", 4.0),
        "models_json":           json.dumps(models_raw),
        "findings_json":         json.dumps({
            "project_owner":  findings.get("owner", ""),
            "project_number": findings.get("number", 0),
            "initial_status": findings.get("name", ""),
        }) if findings else None,
        "stages": stages,
    }

    print(f"  pipeline: {PIPELINE_ID} ({row['name']}) — {len(stages)} stages: "
          + " → ".join(s["column_name"] for s in stages))
    if dry_run:
        return
    await upsert_pipeline(row)


async def main():
    parser = argparse.ArgumentParser(description="Seed gru-server from HIL config.yml")
    parser.add_argument("--config",  default=os.environ.get("GRU_SEED_CONFIG", DEFAULT_CONFIG))
    parser.add_argument("--token",   default=os.environ.get("GRU_GHE_TOKEN"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "SEEDING"
    print(f"▶ {mode} from {args.config}")

    cfg = _load_config(args.config)
    await _insert_connector(cfg, args.token, args.dry_run)
    await _insert_pipeline(cfg, args.dry_run)

    print("✓ Seed complete")


if __name__ == "__main__":
    asyncio.run(main())

