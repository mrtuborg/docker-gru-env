"""Agents router — CRUD, import from file/repo, frontmatter parsing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from ..config import list_agents, get_agent, upsert_agent, delete_agent

router = APIRouter()


# ── Frontmatter parser ────────────────────────────────────────────────────────

def parse_agent_md(content: str) -> dict:
    """Parse .agent.md file into frontmatter dict + body."""
    content = content.strip()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                # Fall back to simple line-by-line parsing for unquoted colons
                frontmatter = {}
                for line in parts[1].splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and v:
                            frontmatter[k] = v
            body = parts[2].strip()
        else:
            frontmatter = {}
            body = content
    else:
        frontmatter = {}
        body = content

    return {
        "name": frontmatter.get("name", ""),
        "description": frontmatter.get("description", ""),
        "model": frontmatter.get("model", ""),
        "tools": frontmatter.get("tools", []),
        "skills": frontmatter.get("skills", []),
        "mcp_servers": frontmatter.get("mcp-servers", frontmatter.get("mcp_servers", {})),
        "body": body,
        "frontmatter": frontmatter,
    }


def build_agent_md(data: dict) -> str:
    """Build .agent.md content from structured data."""
    fm = {}
    if data.get("name"):
        fm["name"] = data["name"]
    if data.get("description"):
        fm["description"] = data["description"]
    if data.get("model"):
        fm["model"] = data["model"]
    if data.get("tools"):
        fm["tools"] = data["tools"]
    if data.get("skills"):
        fm["skills"] = data["skills"]
    if data.get("mcp_servers"):
        fm["mcp-servers"] = data["mcp_servers"]

    parts = []
    if fm:
        parts.append("---")
        parts.append(yaml.dump(fm, default_flow_style=False, sort_keys=False).strip())
        parts.append("---")
    if data.get("body"):
        parts.append("")
        parts.append(data["body"])

    return "\n".join(parts) + "\n"


# ── Request models ────────────────────────────────────────────────────────────

class AgentCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    source: str = "inline"
    agent_md: str = ""
    file_path: str = ""
    repo_url: str = ""
    repo_path: str = ""
    repo_ref: str = "main"
    model: str = ""
    tools: list = []
    skills: list = []
    mcp_servers: dict = {}


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_md: Optional[str] = None
    model: Optional[str] = None
    tools: Optional[list] = None
    skills: Optional[list] = None
    mcp_servers: Optional[dict] = None


class ImportFileRequest(BaseModel):
    file_path: str
    agent_id: Optional[str] = None


class ImportRepoRequest(BaseModel):
    repo_url: str
    repo_path: str = ".github/agents"
    repo_ref: str = "main"


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_all():
    return await list_agents()


@router.post("", status_code=201)
async def create(body: AgentCreate):
    existing = await get_agent(body.id)
    if existing:
        raise HTTPException(409, f"Agent '{body.id}' already exists")

    data = body.dict()

    # If agent_md provided, parse frontmatter to extract metadata
    if data["agent_md"]:
        parsed = parse_agent_md(data["agent_md"])
        if not data["name"] and parsed["name"]:
            data["name"] = parsed["name"]
        if not data["description"] and parsed["description"]:
            data["description"] = parsed["description"]
        if not data["model"] and parsed["model"]:
            data["model"] = parsed["model"]
        if not data["tools"] and parsed["tools"]:
            data["tools"] = parsed["tools"]
        if not data["mcp_servers"] and parsed["mcp_servers"]:
            data["mcp_servers"] = parsed["mcp_servers"]

    await upsert_agent(data)
    return await get_agent(body.id)


@router.get("/{agent_id}")
async def get_one(agent_id: str):
    a = await get_agent(agent_id)
    if not a:
        raise HTTPException(404, "Agent not found")
    return a


@router.put("/{agent_id}")
async def update(agent_id: str, body: AgentUpdate):
    existing = await get_agent(agent_id)
    if not existing:
        raise HTTPException(404, "Agent not found")
    merged = {**existing}
    for k, v in body.dict(exclude_unset=True).items():
        merged[k] = v

    # Re-parse frontmatter if agent_md changed — only override a field if
    # frontmatter actually declares it (non-empty), to avoid wiping explicit values.
    if "agent_md" in body.dict(exclude_unset=True) and merged["agent_md"]:
        parsed = parse_agent_md(merged["agent_md"])
        fm = parsed.get("frontmatter", {})
        if parsed.get("model"):
            merged["model"] = parsed["model"]
        if parsed.get("tools"):
            merged["tools"] = parsed["tools"]
        if "skills" in fm:  # explicitly declared in frontmatter
            merged["skills"] = parsed["skills"]
        if parsed.get("mcp_servers"):
            merged["mcp_servers"] = parsed["mcp_servers"]

    await upsert_agent(merged)
    return await get_agent(agent_id)


@router.delete("/{agent_id}")
async def remove(agent_id: str):
    ok = await delete_agent(agent_id)
    if not ok:
        raise HTTPException(404, "Agent not found")
    return {"deleted": True}


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/import/file", status_code=201)
async def import_from_file(body: ImportFileRequest):
    """Import an agent from a local .agent.md file."""
    path = Path(body.file_path).expanduser()
    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")

    content = path.read_text()
    parsed = parse_agent_md(content)

    # Derive agent_id from filename or explicit parameter
    if body.agent_id:
        aid = body.agent_id
    else:
        stem = path.stem
        if stem.endswith(".agent"):
            stem = stem[:-6]
        aid = re.sub(r'[^a-z0-9-]', '-', stem.lower().strip())

    existing = await get_agent(aid)
    if existing:
        raise HTTPException(409, f"Agent '{aid}' already exists")

    data = {
        "id": aid,
        "name": parsed["name"] or stem,
        "description": parsed["description"],
        "source": "file",
        "agent_md": content,
        "file_path": str(path),
        "model": parsed["model"],
        "tools": parsed["tools"],
        "mcp_servers": parsed["mcp_servers"],
    }
    await upsert_agent(data)
    return await get_agent(aid)


@router.post("/import/upload", status_code=201)
async def import_from_upload(file: UploadFile = File(...)):
    """Import an agent from an uploaded .agent.md file."""
    content = (await file.read()).decode("utf-8")
    parsed = parse_agent_md(content)

    # Derive ID from filename
    stem = file.filename or "uploaded-agent"
    if stem.endswith(".agent.md"):
        stem = stem[:-9]
    elif stem.endswith(".md"):
        stem = stem[:-3]
    aid = re.sub(r'[^a-z0-9-]', '-', stem.lower().strip())

    # Check for collision
    existing = await get_agent(aid)
    if existing:
        # Append suffix
        aid = f"{aid}-{len(await list_agents())}"

    data = {
        "id": aid,
        "name": parsed["name"] or stem,
        "description": parsed["description"],
        "source": "file",
        "agent_md": content,
        "file_path": "",
        "model": parsed["model"],
        "tools": parsed["tools"],
        "mcp_servers": parsed["mcp_servers"],
    }
    await upsert_agent(data)
    return await get_agent(aid)


@router.post("/import/repo", status_code=201)
async def import_from_repo(body: ImportRepoRequest):
    """Import all .agent.md files from a git repository directory."""
    # This would clone/fetch the repo and read .agent.md files
    # For now, we support local paths that look like repo checkouts
    import subprocess

    # Clone to temp dir
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", body.repo_ref,
                 body.repo_url, tmpdir],
                check=True, capture_output=True, text=True, timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise HTTPException(502, f"Git clone failed: {e.stderr}")

        agents_dir = Path(tmpdir) / body.repo_path
        if not agents_dir.exists():
            raise HTTPException(404, f"Path '{body.repo_path}' not found in repo")

        imported = []
        for md_file in sorted(agents_dir.glob("*.agent.md")):
            content = md_file.read_text()
            parsed = parse_agent_md(content)
            stem = md_file.stem
            if stem.endswith(".agent"):
                stem = stem[:-6]
            aid = re.sub(r'[^a-z0-9-]', '-', stem.lower().strip())

            if await get_agent(aid):
                continue  # Skip existing

            data = {
                "id": aid,
                "name": parsed["name"] or stem,
                "description": parsed["description"],
                "source": "repo",
                "agent_md": content,
                "repo_url": body.repo_url,
                "repo_path": f"{body.repo_path}/{md_file.name}",
                "repo_ref": body.repo_ref,
                "model": parsed["model"],
                "tools": parsed["tools"],
                "mcp_servers": parsed["mcp_servers"],
            }
            await upsert_agent(data)
            imported.append(aid)

        return {"imported": imported, "count": len(imported)}
