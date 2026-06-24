"""Pipelines router — CRUD, control, runs, board introspection."""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

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
    # Ensure stages are dicts (from Pydantic models)
    if "stages" in merged and merged["stages"]:
        merged["stages"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
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

@router.post("/{pipeline_id}/start")
async def start_pipeline(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    # TODO: integrate with PipelineEngine when implemented
    return {"status": "started", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/stop")
async def stop_pipeline(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return {"status": "stopped", "pipeline_id": pipeline_id}


@router.post("/{pipeline_id}/run-once")
async def run_once(pipeline_id: str, request: Request):
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return {"status": "triggered", "pipeline_id": pipeline_id}


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


# ── Board introspection ──────────────────────────────────────────────────────

@router.get("/board-columns/{plugin_id}")
async def fetch_board_columns(
    plugin_id: str,
    owner: str,
    number: int,
    request: Request,
):
    """Fetch board column names from GitHub Projects v2 via GraphQL."""
    pm = request.app.state.plugins
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
