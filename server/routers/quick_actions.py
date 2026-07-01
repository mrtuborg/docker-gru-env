"""Quick Actions router — CRUD, generate issue body, publish to GitHub board."""
from __future__ import annotations

import json
import uuid
from typing import Optional

import aiosqlite
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import get_db_path, get_pipeline
from ..vault import load_secret
from ..services.pipeline_engine import _gh_host_for, _detect_entity_type
from .environment import load_env_dict

router = APIRouter()


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _list_quick_actions() -> list[dict]:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM quick_actions ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d.pop("config_json", "{}"))
                result.append(d)
            return result


async def _get_quick_action(action_id: str) -> dict | None:
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM quick_actions WHERE id=?", (action_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            d = dict(row)
            d["config"] = json.loads(d.pop("config_json", "{}"))
            return d


async def _upsert_quick_action(data: dict) -> None:
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO quick_actions(id, name, action_type, pipeline_id, config_json, updated_at)
               VALUES(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, action_type=excluded.action_type,
                   pipeline_id=excluded.pipeline_id, config_json=excluded.config_json,
                   updated_at=excluded.updated_at""",
            (
                data["id"], data["name"], data.get("action_type", "create_issue"),
                data.get("pipeline_id", ""),
                json.dumps(data.get("config", {})),
            ),
        )
        await db.commit()


async def _delete_quick_action(action_id: str) -> bool:
    async with aiosqlite.connect(get_db_path()) as db:
        cur = await db.execute("DELETE FROM quick_actions WHERE id=?", (action_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Request models ────────────────────────────────────────────────────────────

class QuickActionCreate(BaseModel):
    name: str
    action_type: str = "create_issue"
    pipeline_id: str = ""
    config: dict = {}


class QuickActionUpdate(BaseModel):
    name: Optional[str] = None
    action_type: Optional[str] = None
    pipeline_id: Optional[str] = None
    config: Optional[dict] = None


class GenerateRequest(BaseModel):
    pipeline_id: str
    stage: str          # column name the issue should start in
    title: str
    extra_context: str = ""
    skill: str = ""     # optional skill script path (relative to working_dir)


class PublishRequest(BaseModel):
    pipeline_id: str
    stage: str
    repo: str           # owner/repo to create the issue in
    title: str
    body: str
    labels: list[str] = []
    skill: str = ""     # if set and skill has create.sh, delegate publish entirely


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_actions():
    return await _list_quick_actions()


@router.post("", status_code=201)
async def create_action(body: QuickActionCreate):
    action_id = "qa-" + uuid.uuid4().hex[:8]
    data = {"id": action_id, **body.model_dump()}
    await _upsert_quick_action(data)
    return await _get_quick_action(action_id)


@router.get("/{action_id}")
async def get_action(action_id: str):
    a = await _get_quick_action(action_id)
    if not a:
        raise HTTPException(404, "Quick action not found")
    return a


@router.put("/{action_id}")
async def update_action(action_id: str, body: QuickActionUpdate):
    existing = await _get_quick_action(action_id)
    if not existing:
        raise HTTPException(404, "Quick action not found")
    merged = {**existing}
    for k, v in body.model_dump(exclude_unset=True).items():
        merged[k] = v
    await _upsert_quick_action(merged)
    return await _get_quick_action(action_id)


@router.delete("/{action_id}")
async def delete_action(action_id: str):
    ok = await _delete_quick_action(action_id)
    if not ok:
        raise HTTPException(404, "Quick action not found")
    return {"deleted": True}


# ── Generate ──────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate_issue(body: GenerateRequest, request: Request):
    """Generate an issue body via a skill script or GitHub Models API."""
    pipeline = await get_pipeline(body.pipeline_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    working_dir = pipeline.get("working_dir") or "/workspace"

    # ── Skill-based generation ────────────────────────────────────────────────
    if body.skill:
        import asyncio
        from pathlib import Path

        # Resolve skill folder → script.
        # body.skill is a skill ID (folder name), e.g. "hil-stress".
        # Search: /workspace/skills/<id>/ then ~/.copilot/skills/<id>/
        skill_id = body.skill
        search_roots = [
            Path("/workspace/skills"),
            Path.home() / ".copilot" / "skills",
        ]
        skill_dir: Path | None = None
        for root in search_roots:
            candidate = root / skill_id
            if candidate.is_dir():
                skill_dir = candidate
                break

        if skill_dir is None:
            raise HTTPException(404, f"Skill '{skill_id}' not found")

        # Pick entry-point script: run.sh → generate.sh → first create-*.sh → first *.sh
        def _find_script(d: Path) -> Path | None:
            for name in ["run.sh", "generate.sh"]:
                p = d / name
                if p.exists():
                    return p
            creates = sorted(d.glob("create-*.sh"))
            if creates:
                return creates[0]
            scripts = sorted(d.glob("*.sh"))
            return scripts[0] if scripts else None

        script = _find_script(skill_dir)
        if script is None:
            raise HTTPException(404, f"No runnable script found in skill '{skill_id}'")

        cmd = ["bash", str(script), body.title, body.extra_context]
        env_vars = await load_env_dict()
        env = {**__import__("os").environ, **env_vars}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_dir),
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return {"body": stdout.decode().strip(), "source": "skill"}
            else:
                err = stderr.decode().strip()
                raise HTTPException(500, f"Skill '{skill_id}' failed (exit {proc.returncode}): {err}")
        except asyncio.TimeoutError:
            raise HTTPException(504, f"Skill '{skill_id}' timed out after 30s")


    # ── LLM generation (fallback) ─────────────────────────────────────────────
    plugin_id = pipeline.get("plugin_id", "")
    token = await load_secret(plugin_id, "token") if plugin_id else None
    if not token:
        raise HTTPException(400, "No connector token — cannot call LLM")

    gh_host = _gh_host_for(plugin_id)

    stage_cfg = next(
        (s for s in pipeline.get("stages", []) if s.get("column_name") == body.stage), {}
    )
    agent_id = stage_cfg.get("agent_id", "")

    system_prompt = (
        "You are a technical writer helping an engineering team create GitHub issues "
        "for a hardware-in-the-loop (HIL) testing pipeline. "
        "Write a clear, concise issue body in Markdown. "
        "Include a short description, acceptance criteria, and any required fields "
        "(device serial, firmware version) as placeholders if not provided. "
        "Keep it under 300 words. Do not include the title."
    )

    user_prompt = (
        f"Create a GitHub issue body for the following:\n\n"
        f"Title: {body.title}\n"
        f"Pipeline stage: {body.stage}\n"
        + (f"Stage agent: {agent_id}\n" if agent_id else "")
        + (f"Additional context: {body.extra_context}\n" if body.extra_context else "")
    )

    # Try GitHub Models API (works with GHE PAT and github.com PAT)
    models_url = (
        f"https://{gh_host}/api/v3/models/chat/completions"
        if gh_host != "github.com"
        else "https://models.inference.ai.azure.com/chat/completions"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                models_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )
        if resp.status_code == 200:
            data = resp.json()
            generated = data["choices"][0]["message"]["content"].strip()
            return {"body": generated}
    except Exception:
        pass

    # Fallback: simple template
    generated = (
        f"## Description\n\n{body.title}\n\n"
        f"## Target stage\n\n`{body.stage}`\n\n"
        f"## Required fields\n\n"
        f"- device: `<serial>`\n"
        f"- firmware: `<version>`\n\n"
        + (f"## Context\n\n{body.extra_context}\n" if body.extra_context else "")
        + "\n## Acceptance criteria\n\n- [ ] Pipeline processes this issue successfully\n"
    )
    return {"body": generated}


# ── Publish ───────────────────────────────────────────────────────────────────

@router.post("/publish")
async def publish_issue(body: PublishRequest):
    """Create a GitHub issue and add it to the project board in the given stage."""
    pipeline = await get_pipeline(body.pipeline_id)
    if not pipeline:
        raise HTTPException(404, "Pipeline not found")

    plugin_id = pipeline.get("plugin_id", "")
    token = await load_secret(plugin_id, "token") if plugin_id else None
    if not token:
        raise HTTPException(400, "No connector token")

    gh_host = _gh_host_for(plugin_id)

    # ── Skill-based publish (create.sh) ──────────────────────────────────────
    if body.skill:
        import asyncio
        from pathlib import Path

        skill_id = body.skill
        skill_dir: Path | None = None
        for root in [Path("/workspace/skills"), Path.home() / ".copilot" / "skills"]:
            candidate = root / skill_id
            if candidate.is_dir():
                skill_dir = candidate
                break

        create_sh = (skill_dir / "create.sh") if skill_dir else None
        if create_sh and create_sh.exists():
            env_vars = await load_env_dict()
            env = {
                **__import__("os").environ,
                **env_vars,
                "GH_TOKEN": token,
                "GH_HOST": gh_host,
                "WORKSPACE": pipeline.get("working_dir") or "/workspace",
            }
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bash", str(create_sh), body.title, body.body,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(skill_dir),
                    env=env,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode == 0:
                    return {"message": stdout.decode().strip(), "source": "skill"}
                else:
                    err = stderr.decode().strip()
                    raise HTTPException(500, f"Skill create.sh failed: {err}")
            except asyncio.TimeoutError:
                raise HTTPException(504, f"Skill create.sh timed out after 120s")

    api_base = (
        f"https://{gh_host}/api/v3"
        if gh_host != "github.com"
        else "https://api.github.com"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }

    # 1. Create the issue
    async with httpx.AsyncClient(timeout=30) as client:
        create_resp = await client.post(
            f"{api_base}/repos/{body.repo}/issues",
            headers=headers,
            json={"title": body.title, "body": body.body, "labels": body.labels},
        )
    if create_resp.status_code not in (200, 201):
        raise HTTPException(create_resp.status_code, f"GitHub issue creation failed: {create_resp.text}")

    issue_data = create_resp.json()
    issue_number = issue_data["number"]
    issue_node_id = issue_data["node_id"]

    # 2. Add to project board
    project_owner = pipeline.get("project_owner", "")
    project_number = pipeline.get("project_number", 0)

    if project_owner and project_number:
        gql_url = (
            f"https://{gh_host}/api/graphql"
            if gh_host != "github.com"
            else "https://api.github.com/graphql"
        )
        gql_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        entity = await _detect_entity_type(gh_host, project_owner, token)

        # Get project node ID
        proj_query = """
        query { %s(login: "%s") { projectV2(number: %d) { id fields(first:20) {
          nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
        } } } }
        """ % (entity, project_owner, project_number)

        async with httpx.AsyncClient(timeout=20) as client:
            proj_resp = await client.post(gql_url, headers=gql_headers, json={"query": proj_query})
        proj_data = proj_resp.json()
        proj = (proj_data.get("data", {}).get(entity, {}) or {}).get("projectV2", {})
        proj_id = proj.get("id")

        if proj_id:
            # Add issue item to project
            add_mutation = """
            mutation { addProjectV2ItemById(input: {projectId: "%s", contentId: "%s"}) {
              item { id fieldValues(first:20) { nodes {
                ... on ProjectV2ItemFieldSingleSelectValue { id field { ... on ProjectV2SingleSelectField { id name options { id name } } } }
              } } }
            } }
            """ % (proj_id, issue_node_id)

            async with httpx.AsyncClient(timeout=20) as client:
                add_resp = await client.post(gql_url, headers=gql_headers, json={"query": add_mutation})
            add_data = add_resp.json()
            item = add_data.get("data", {}).get("addProjectV2ItemById", {}).get("item", {})
            item_id = item.get("id")

            # Set Status field to the target stage
            if item_id and body.stage:
                fields = proj.get("fields", {}).get("nodes", [])
                status_field = next((f for f in fields if f.get("name") == "Status"), None)
                if status_field:
                    option = next(
                        (o for o in status_field.get("options", []) if o["name"] == body.stage),
                        None
                    )
                    if option:
                        set_mutation = """
                        mutation { updateProjectV2ItemFieldValue(input: {
                          projectId: "%s", itemId: "%s",
                          fieldId: "%s", value: { singleSelectOptionId: "%s" }
                        }) { projectV2Item { id } } }
                        """ % (proj_id, item_id, status_field["id"], option["id"])
                        async with httpx.AsyncClient(timeout=20) as client:
                            await client.post(gql_url, headers=gql_headers, json={"query": set_mutation})

    return {
        "issue_number": issue_number,
        "issue_url": issue_data.get("html_url"),
        "stage": body.stage,
    }
