"""Pipelines router — CRUD, control, runs, board introspection."""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import (
    get_pipeline, list_pipelines, upsert_pipeline, delete_pipeline,
    list_pipeline_runs, get_pipeline_run_items,
    clear_pipeline_state, get_pipeline_state,
)
from ..models.pipeline import PipelineCreate, PipelineUpdate
from ..vault import load_secret

router = APIRouter()


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_all():
    return await list_pipelines()


@router.post("", status_code=201)
async def create(body: PipelineCreate):
    existing = await get_pipeline(body.id)
    if existing:
        raise HTTPException(409, f"Pipeline '{body.id}' already exists")
    await upsert_pipeline(body.model_dump())
    return await get_pipeline(body.id)


# ── Import from config.yml ───────────────────────────────────────────────────

class ImportRequest(BaseModel):
    config_yaml: str
    prompts_dir: Optional[str] = None
    pipeline_id: Optional[str] = None


@router.post("/import", status_code=201)
async def import_pipeline(body: ImportRequest):
    """Import a pipeline from an existing watcher config.yml + stage-prompts/."""
    try:
        cfg = yaml.safe_load(body.config_yaml)
    except Exception as e:
        raise HTTPException(400, f"Invalid YAML: {e}")

    if not cfg or not isinstance(cfg, dict):
        raise HTTPException(400, "Config must be a YAML mapping")

    # Extract pipeline identity
    pid = body.pipeline_id or cfg.get("name", "imported-" + uuid.uuid4().hex[:6])
    pid = re.sub(r'[^a-z0-9-]', '-', pid.lower().strip())

    existing = await get_pipeline(pid)
    if existing:
        raise HTTPException(409, f"Pipeline '{pid}' already exists")

    # Build stages — support both new `stages` array and legacy `stage_order` list
    stages = []
    if cfg.get("stages") and isinstance(cfg["stages"], list):
        # New format: stages array with column/actor/prompt/etc
        for s in cfg["stages"]:
            if not isinstance(s, dict):
                continue
            stages.append({
                "column": s.get("column", ""),
                "actor": s.get("actor", "ai"),
                "agent_id": s.get("agent_id", ""),
                "task_prompt": s.get("task_prompt", ""),
                "prompt": s.get("prompt", ""),
                "on_success": s.get("on_success", ""),
                "on_failure": s.get("on_failure", ""),
                "on_timeout": s.get("on_timeout", ""),
                "env": s.get("env", {}),
            })
    else:
        # Legacy format: stage_order list of column names
        stage_order = cfg.get("stage_order", [])
        prompts_dir = Path(body.prompts_dir) if body.prompts_dir else None
        for col_name in stage_order:
            prompt = ""
            if prompts_dir:
                for candidate in [col_name, col_name.replace(" ", "_")]:
                    prompt_file = prompts_dir / f"{candidate}.md"
                    if prompt_file.exists():
                        prompt = prompt_file.read_text()
                        break
            stages.append({
                "column": col_name,
                "actor": "ai",
                "prompt": prompt,
            })
        # Add human gate stages that aren't in stage_order but are common
        for human_col in ["Backlog", "Review", "Done"]:
            if human_col not in stage_order and human_col not in [s["column"] for s in stages]:
                stages.append({"column": human_col, "actor": "human"})

    # Build models list
    models = []
    if cfg.get("models"):
        for m in cfg["models"]:
            if isinstance(m, dict):
                models.append({"model": m.get("model", ""), "priority": m.get("priority", 1)})
            elif isinstance(m, str):
                models.append({"model": m, "priority": len(models) + 1})
    elif cfg.get("model"):
        models.append({"model": cfg["model"], "priority": 1})

    # Build findings board
    findings = None
    fp = cfg.get("findings_project", {})
    if fp and isinstance(fp, dict):
        findings = {
            "project_owner": fp.get("project_owner", cfg.get("project_owner", "")),
            "project_number": fp.get("project_number", 0),
            "initial_status": fp.get("initial_status", "Analysis"),
        }

    pipeline_data = {
        "id": pid,
        "name": cfg.get("name", pid),
        "enabled": False,  # Don't auto-start imported pipelines
        "plugin_id": cfg.get("plugin_id", "github-sensio"),
        "board_type": "github",
        "project_owner": cfg.get("project_owner", ""),
        "project_number": cfg.get("project_number", 0),
        "stages": stages,
        "poll_interval": cfg.get("poll_interval", 300),
        "max_issues": cfg.get("max_issues", 50),
        "max_retries": cfg.get("max_per_issue", 3),
        "session_timeout_hours": cfg.get("session_timeout_hours", 4.0),
        "models": models,
        "allowed_repos": cfg.get("allowed_repos", []),
        "findings": findings,
    }

    await upsert_pipeline(pipeline_data)
    return await get_pipeline(pid)


@router.get("/{pipeline_id}")
async def get_one(pipeline_id: str):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return p


@router.put("/{pipeline_id}")
async def update(pipeline_id: str, body: PipelineUpdate):
    existing = await get_pipeline(pipeline_id)
    if not existing:
        raise HTTPException(404, "Pipeline not found")
    merged = {**existing}
    for k, v in body.model_dump(exclude_unset=True).items():
        merged[k] = v
    # Normalize stages: DB returns column_name, upsert expects column
    if "stages" in merged and merged["stages"]:
        merged["stages"] = [
            _fix_stage_keys(s.model_dump() if hasattr(s, "model_dump") else s)
            for s in merged["stages"]
        ]
    await upsert_pipeline(merged)
    return await get_pipeline(pipeline_id)


@router.delete("/{pipeline_id}")
async def remove(pipeline_id: str):
    ok = await delete_pipeline(pipeline_id)
    if not ok:
        raise HTTPException(404, "Pipeline not found")
    return {"deleted": True}


# ── Control ───────────────────────────────────────────────────────────────────

def _fix_stage_keys(stage: dict) -> dict:
    """get_pipeline() returns stages with 'column_name'; upsert_pipeline() needs 'column'."""
    if "column_name" in stage and "column" not in stage:
        return {**stage, "column": stage["column_name"]}
    return stage


@router.post("/{pipeline_id}/start")
async def start_pipeline(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    engine = request.app.state.engine
    started = await engine.start(pipeline_id)
    if not started:
        return {"status": "already_running", "pipeline_id": pipeline_id}
    p["stages"] = [_fix_stage_keys(s) for s in p.get("stages", [])]
    await upsert_pipeline({**p, "enabled": True})
    return {"status": "started", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/stop")
async def stop_pipeline(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    engine = request.app.state.engine
    await engine.stop(pipeline_id)
    p["stages"] = [_fix_stage_keys(s) for s in p.get("stages", [])]
    await upsert_pipeline({**p, "enabled": False})
    return {"status": "stopped", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/run-once")
async def run_once(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    engine = request.app.state.engine
    result = await engine.run_once(pipeline_id)
    return {"status": "completed", "pipeline_id": pipeline_id, "result": result}


# ── Observability ─────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}/runs")
async def get_runs(pipeline_id: str, limit: int = 50):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return await list_pipeline_runs(pipeline_id, limit)


@router.get("/{pipeline_id}/runs/{run_id}/items")
async def get_run_items(pipeline_id: str, run_id: str):
    return await get_pipeline_run_items(run_id)


@router.get("/{pipeline_id}/state")
async def get_state(pipeline_id: str):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return await get_pipeline_state(pipeline_id)


@router.delete("/{pipeline_id}/state")
async def clear_state(pipeline_id: str):
    await clear_pipeline_state(pipeline_id)
    return {"cleared": True}


@router.get("/{pipeline_id}/status")
async def get_status(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    engine = request.app.state.engine
    live = engine.live_state(pipeline_id)
    queued = live["queued"]

    # When engine is stopped/paused, still fetch from GitHub so Boards shows current state
    if not queued and engine.status(pipeline_id) in ("stopped", "paused"):
        try:
            from ..vault import load_secret  # noqa: PLC0415
            plugin_id = p.get("plugin_id", "")
            token = await load_secret(plugin_id, "token") if plugin_id else None
            if token:
                issues = await engine._query_board(p, token)
                stages = {s.get("column") or s.get("column_name", "") for s in (p.get("stages") or [])}
                queued = [
                    {"number": i.number, "repo": i.repo, "stage": i.stage, "title": i.title}
                    for i in issues if i.stage in stages
                ]
        except Exception:
            pass  # best-effort; don't fail the endpoint

    recent = await list_pipeline_runs(pipeline_id, limit=20)
    recent_items: list[dict] = []
    for run in recent[:5]:
        items = await get_pipeline_run_items(run["id"])
        for item in items:
            recent_items.append({**item, "run_id": run["id"]})
    return {
        "pipeline_id": pipeline_id,
        "status": engine.status(pipeline_id),
        "active": live["active"],
        "queued": queued,
        "recent": recent_items[:20],
    }


@router.get("/{pipeline_id}/logs")
async def stream_logs(pipeline_id: str, request: Request):
    """SSE stream of live pipeline log events."""
    from sse_starlette.sse import EventSourceResponse
    from ..services.pipeline_engine import log_bus

    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")

    queue = log_bus.subscribe(pipeline_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": event.level, "data": json.dumps(event.to_dict())}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            log_bus.unsubscribe(pipeline_id, queue)

    return EventSourceResponse(event_generator())


# ── Board introspection ──────────────────────────────────────────────────────

@router.get("/board-columns/{plugin_id}")
async def fetch_board_columns(
    plugin_id: str,
    owner: str,
    number: int,
    request: Request,
):
    """Fetch board column names from GitHub Projects v2 via GraphQL."""
    pm = request.app.state.connectors
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")

    token = await load_secret(plugin_id, "token")
    if not token:
        raise HTTPException(401, "Plugin has no token — authorize first")

    host = plugin._config.get("host", "github.com")
    gql_url = (
        f"https://{host}/api/graphql"
        if host != "github.com"
        else "https://api.github.com/graphql"
    )

    # Detect org vs user
    entity = await _detect_entity(host, owner, token)

    query = """
    query($owner: String!, $number: Int!) {
      %s(login: $owner) {
        projectV2(number: $number) {
          field(name: "Status") {
            ... on ProjectV2SingleSelectField {
              options { name }
            }
          }
        }
      }
    }
    """ % entity

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            gql_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": {"owner": owner, "number": number}},
        )
    if resp.status_code != 200:
        raise HTTPException(502, f"GitHub API returned {resp.status_code}")

    data = resp.json()
    errors = data.get("errors")
    if errors:
        raise HTTPException(502, errors[0].get("message", str(errors)))

    ent_data = data.get("data", {}).get(entity, {})
    project = ent_data.get("projectV2")
    if not project:
        raise HTTPException(404, f"Project #{number} not found for {owner}")

    field = project.get("field")
    if not field:
        raise HTTPException(404, "Status field not found on board")

    columns = [opt["name"] for opt in field.get("options", [])]
    return {"columns": columns, "owner": owner, "number": number}


async def _detect_entity(host: str, owner: str, token: str) -> str:
    """Detect whether owner is 'organization' or 'user'."""
    api_base = (
        f"https://{host}/api/v3"
        if host != "github.com"
        else "https://api.github.com"
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{api_base}/orgs/{owner}",
            headers={"Authorization": f"Bearer {token}"},
        )
    return "organization" if resp.status_code == 200 else "user"
