"""Skills router — read/write skill files from ~/.copilot/skills/ and /workspace/skills/."""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()

# Writable skills dir (installed into Copilot CLI's data home)
_SKILLS_HOME = Path(os.environ.get("COPILOT_DATA_HOME", Path.home() / ".copilot")) / "skills"
# Read-only workspace skills (mounted from host)
_WORKSPACE_SKILLS = Path("/workspace/skills")


def _skills_root() -> Path:
    """Primary skills dir — prefer writable home, fall back to workspace."""
    if _SKILLS_HOME.exists():
        return _SKILLS_HOME
    if _WORKSPACE_SKILLS.exists():
        return _WORKSPACE_SKILLS
    return _SKILLS_HOME  # will be created on write


def _all_skills() -> list[dict]:
    """Collect skills from both locations, workspace takes precedence by name."""
    seen: dict[str, dict] = {}

    def _read_dir(root: Path, writable: bool):
        if not root.exists():
            return
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            skill_md = d / "SKILL.md"
            description = ""
            if skill_md.exists():
                for line in skill_md.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        description = line
                        break
            files = sorted(
                f.name for f in d.iterdir()
                if f.is_file() and not f.name.startswith(".")
            )
            seen[d.name] = {
                "id": d.name,
                "name": d.name.replace("-", " ").title(),
                "description": description,
                "files": files,
                "writable": writable,
                "path": str(d),
            }

    _read_dir(_WORKSPACE_SKILLS, writable=False)
    _read_dir(_SKILLS_HOME, writable=True)
    return list(seen.values())


def _skill_dir(skill_id: str) -> tuple[Path, bool]:
    """Return (path, writable) for a skill, checking both locations."""
    # Prefer writable home
    p = _SKILLS_HOME / skill_id
    if p.exists():
        return p, True
    p = _WORKSPACE_SKILLS / skill_id
    if p.exists():
        return p, False
    raise HTTPException(404, f"Skill '{skill_id}' not found")


def _safe_path(skill_dir: Path, filename: str) -> Path:
    p = (skill_dir / filename).resolve()
    if not str(p).startswith(str(skill_dir.resolve())):
        raise HTTPException(400, "Invalid filename")
    return p


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("")
async def list_skills():
    return _all_skills()


# ── Single skill ──────────────────────────────────────────────────────────────

@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    d, writable = _skill_dir(skill_id)
    files = []
    for f in sorted(d.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append({"name": f.name, "size": f.stat().st_size})
    skill_md = d / "SKILL.md"
    description = ""
    if skill_md.exists():
        for line in skill_md.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line
                break
    return {
        "id": skill_id,
        "name": skill_id.replace("-", " ").title(),
        "description": description,
        "files": files,
        "writable": writable,
        "path": str(d),
    }


# ── File read ─────────────────────────────────────────────────────────────────

@router.get("/{skill_id}/files/{filename:path}")
async def read_file(skill_id: str, filename: str):
    d, _ = _skill_dir(skill_id)
    p = _safe_path(d, filename)
    if not p.exists():
        raise HTTPException(404, f"File '{filename}' not found in skill '{skill_id}'")
    return {"skill_id": skill_id, "filename": filename, "content": p.read_text(errors="replace")}


# ── File write ────────────────────────────────────────────────────────────────

class FileWrite(BaseModel):
    content: str


@router.put("/{skill_id}/files/{filename:path}")
async def write_file(skill_id: str, filename: str, body: FileWrite):
    """Write a file. Copies skill from workspace to home first if it's read-only."""
    try:
        d, writable = _skill_dir(skill_id)
    except HTTPException:
        # Skill doesn't exist yet — create in home
        d = _SKILLS_HOME / skill_id
        writable = True

    if not writable:
        # Copy entire skill from workspace to home so edits don't touch the mount
        import shutil
        dest = _SKILLS_HOME / skill_id
        dest.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        d = dest

    d.mkdir(parents=True, exist_ok=True)
    p = _safe_path(d, filename)
    p.write_text(body.content)
    return {"skill_id": skill_id, "filename": filename, "size": p.stat().st_size}


# ── Create skill ──────────────────────────────────────────────────────────────

class SkillCreate(BaseModel):
    id: str
    name: str = ""
    description: str = ""


@router.post("", status_code=201)
async def create_skill(body: SkillCreate):
    sid = body.id.lower().replace(" ", "-")
    d = _SKILLS_HOME / sid
    if d.exists():
        raise HTTPException(409, f"Skill '{sid}' already exists")
    d.mkdir(parents=True)
    skill_md = d / "SKILL.md"
    display = body.name or sid.replace("-", " ").title()
    skill_md.write_text(f"# {display}\n\n{body.description or 'TODO: describe this skill.'}\n")
    return {"id": sid, "name": display, "description": body.description, "files": ["SKILL.md"], "writable": True, "path": str(d)}


# ── Delete skill ──────────────────────────────────────────────────────────────

@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    d = _SKILLS_HOME / skill_id
    if not d.exists():
        raise HTTPException(404, f"Skill '{skill_id}' not in writable skills dir — cannot delete workspace skills")
    import shutil
    shutil.rmtree(d)
    return {"deleted": True}


# ── Sync from workspace ───────────────────────────────────────────────────────

@router.post("/sync/workspace")
async def sync_from_workspace():
    """Copy all skills from /workspace/skills/ into ~/.copilot/skills/."""
    import shutil
    if not _WORKSPACE_SKILLS.exists():
        raise HTTPException(404, "/workspace/skills not found — is the workspace mounted?")
    copied = []
    for src in sorted(_WORKSPACE_SKILLS.iterdir()):
        if not src.is_dir():
            continue
        dest = _SKILLS_HOME / src.name
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        copied.append(src.name)
    return {"synced": copied, "count": len(copied)}


# ── Export skill as zip ───────────────────────────────────────────────────────

@router.get("/{skill_id}/export")
async def export_skill(skill_id: str):
    """Download all files of a skill as a zip archive."""
    d, _ = _skill_dir(skill_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                zf.write(f, arcname=f"{skill_id}/{f.name}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{skill_id}.zip"'},
    )


# ── Import skill from zip upload ──────────────────────────────────────────────

@router.post("/import/zip", status_code=201)
async def import_skill_zip(file: UploadFile = File(...)):
    """Upload a zip archive (either skill-id/file or flat files) to create/update a skill."""
    data = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Uploaded file is not a valid zip archive")

    names = zf.namelist()
    if not names:
        raise HTTPException(400, "Zip archive is empty")

    # Detect layout: if all entries share a common top-level dir, use it as skill id
    prefixes = {n.split("/")[0] for n in names if n.split("/")[0]}
    if len(prefixes) == 1:
        skill_id = prefixes.pop().lower()
        # Strip the top-level dir from paths
        entries = {n: n[len(skill_id) + 1:] for n in names if "/" in n and not n.endswith("/")}
    else:
        # Flat zip — derive skill id from filename
        stem = (file.filename or "imported-skill").rsplit(".", 1)[0]
        skill_id = stem.lower().replace(" ", "-")
        entries = {n: n for n in names if not n.endswith("/")}

    if not skill_id or not entries:
        raise HTTPException(400, "Could not determine skill id or files from zip")

    dest = _SKILLS_HOME / skill_id
    dest.mkdir(parents=True, exist_ok=True)

    written = []
    for arc_name, rel_name in entries.items():
        if not rel_name:
            continue
        target = _safe_path(dest, rel_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(arc_name))
        # Restore executable bit for shell scripts
        if rel_name.endswith(".sh"):
            target.chmod(target.stat().st_mode | 0o111)
        written.append(rel_name)

    return {
        "id": skill_id,
        "name": skill_id.replace("-", " ").title(),
        "files_written": written,
        "count": len(written),
        "writable": True,
        "path": str(dest),
    }


# ── Upload individual file into existing skill ────────────────────────────────

@router.post("/{skill_id}/files/upload", status_code=201)
async def upload_skill_file(skill_id: str, file: UploadFile = File(...)):
    """Upload a single file into an existing skill (creates skill if missing)."""
    try:
        d, writable = _skill_dir(skill_id)
    except HTTPException:
        d = _SKILLS_HOME / skill_id
        writable = True

    if not writable:
        import shutil
        dest = _SKILLS_HOME / skill_id
        dest.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        d = dest

    d.mkdir(parents=True, exist_ok=True)
    filename = file.filename or "uploaded"
    p = _safe_path(d, filename)
    data = await file.read()
    p.write_bytes(data)
    if filename.endswith(".sh"):
        p.chmod(p.stat().st_mode | 0o111)
    return {"skill_id": skill_id, "filename": filename, "size": len(data)}
