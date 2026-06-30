#!/usr/bin/env python3
"""
Seed the gru-server DB with the HIL stress-test pipeline configuration.

Usage (inside container):
    python /app/seed.py [--token TOKEN] [--dry-run]

TOKEN is the GHE personal access token for sensio.ghe.com.
If omitted, reads GRU_GHE_TOKEN env var.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

# ── Config ────────────────────────────────────────────────────────────────────

CONNECTOR_ID   = "ghe-roommate"
CONNECTOR_TYPE = "github"
CONNECTOR_CFG  = {
    "host":           "sensio.ghe.com",
    "project_owner":  "roommate",
    "project_number": 14,
}

PIPELINE_ID   = "hil-stress"
PIPELINE_NAME = "HIL Stress Test"
PIPELINE_CFG  = {
    "plugin_id":             CONNECTOR_ID,
    "board_type":            "github",
    "project_owner":         "roommate",
    "project_number":        14,
    "poll_interval":         300,
    "max_issues":            10,
    "max_retries":           3,
    "session_timeout_hours": 4.0,
    "enabled":               0,  # paused until user starts it
    "models": [
        {"model": "claude-sonnet-4.6", "priority": 1},
        {"model": "claude-haiku-4.5",  "priority": 2},
    ],
    "findings": {
        "project_owner":  "roommate",
        "project_number": 13,
        "initial_status": "Analysis",
    },
}

# Stage order from config.yml — actor ai for all; human for Review (not listed = final)
STAGES = [
    {"column_name": "Todo",      "actor": "ai",    "stage_index": 0},
    {"column_name": "HW-Check",  "actor": "ai",    "stage_index": 1},
    {"column_name": "HW-Update", "actor": "ai",    "stage_index": 2},
    {"column_name": "HW-Stress", "actor": "ai",    "stage_index": 3},
    {"column_name": "HW-Log",    "actor": "ai",    "stage_index": 4},
    {"column_name": "Review",    "actor": "human",  "stage_index": 5},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _data_dir() -> str:
    return os.environ.get("GRU_DATA_DIR", "/data")


async def _insert_connector(token: str | None, dry_run: bool):
    sys.path.insert(0, "/app")
    from server.config import init_db, upsert_plugin
    from server.vault import store_secret

    print(f"  connector: {CONNECTOR_ID} ({CONNECTOR_TYPE}) → {CONNECTOR_CFG['host']}")
    if dry_run:
        return

    os.environ.setdefault("GRU_DATA_DIR", _data_dir())
    await init_db()
    await upsert_plugin(CONNECTOR_ID, CONNECTOR_TYPE, CONNECTOR_CFG, enabled=True)
    if token:
        await store_secret(CONNECTOR_ID, "token", token)
        print("  token stored in vault ✓")
    else:
        print("  no token provided — authorize via UI after start")


async def _insert_pipeline(dry_run: bool):
    from server.config import upsert_pipeline

    models_json = json.dumps(PIPELINE_CFG.pop("models", []))
    findings_json = json.dumps(PIPELINE_CFG.pop("findings", None))
    enabled = PIPELINE_CFG.pop("enabled", 0)

    row = {
        "id":                    PIPELINE_ID,
        "name":                  PIPELINE_NAME,
        "enabled":               enabled,
        "plugin_id":             PIPELINE_CFG["plugin_id"],
        "board_type":            PIPELINE_CFG["board_type"],
        "project_owner":         PIPELINE_CFG["project_owner"],
        "project_number":        PIPELINE_CFG["project_number"],
        "poll_interval":         PIPELINE_CFG["poll_interval"],
        "max_issues":            PIPELINE_CFG["max_issues"],
        "max_retries":           PIPELINE_CFG["max_retries"],
        "session_timeout_hours": PIPELINE_CFG["session_timeout_hours"],
        "models_json":           models_json,
        "findings_json":         findings_json,
        "stages":                STAGES,
    }
    print(f"  pipeline: {PIPELINE_ID} ({PIPELINE_NAME}) — {len(STAGES)} stages")
    if dry_run:
        return
    await upsert_pipeline(row)


async def main():
    parser = argparse.ArgumentParser(description="Seed gru-server with HIL pipeline")
    parser.add_argument("--token", default=os.environ.get("GRU_GHE_TOKEN"), help="GHE PAT token")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be inserted, don't write")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "SEEDING"
    print(f"▶ {mode} gru-server database at {_data_dir()}")

    await _insert_connector(args.token, args.dry_run)
    await _insert_pipeline(args.dry_run)

    print("✓ Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
