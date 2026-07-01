"""Environment router — global variables, secrets, and file uploads for use by skills and pipelines."""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import get_db_path
from ..vault import _encrypt, _decrypt, load_vault_key  # noqa: internal helpers

router = APIRouter()

# Files stored here (writable, persisted in container data volume)
_ENV_FILES_DIR = Path(os.environ.get("GRU_DATA_DIR", Path.home() / ".gru")) / "env" / "files"

# ── DB helpers ────────────────────────────────────────────────────────────────

async def _ensure_tables():
    async with aiosqlite.connect(get_db_path()) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS env_variables (
                name       TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS env_secrets (
                name       TEXT PRIMARY KEY,
                value      BLOB NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
        """)
        await db.commit()


# ── Variables ─────────────────────────────────────────────────────────────────

class VarUpsert(BaseModel):
    name: str
    value: str
    description: str = ""


@router.get("/variables")
async def list_variables():
    await _ensure_tables()
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name, value, description, updated_at FROM env_variables ORDER BY name") as cur:
            return [dict(r) for r in await cur.fetchall()]


@router.put("/variables/{name}")
async def upsert_variable(name: str, body: VarUpsert):
    await _ensure_tables()
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO env_variables(name, value, description, updated_at)
               VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(name) DO UPDATE SET
                 value=excluded.value, description=excluded.description,
                 updated_at=excluded.updated_at""",
            (name, body.value, body.description),
        )
        await db.commit()
    return {"name": name, "value": body.value}


@router.delete("/variables/{name}")
async def delete_variable(name: str):
    await _ensure_tables()
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM env_variables WHERE name=?", (name,))
        await db.commit()
    return {"deleted": name}


# ── Secrets ───────────────────────────────────────────────────────────────────

class SecretUpsert(BaseModel):
    name: str
    value: str
    description: str = ""


@router.get("/secrets")
async def list_secrets():
    """Return secret metadata only — never the values."""
    await _ensure_tables()
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name, description, updated_at FROM env_secrets ORDER BY name") as cur:
            return [dict(r) for r in await cur.fetchall()]


@router.put("/secrets/{name}")
async def upsert_secret(name: str, body: SecretUpsert):
    await _ensure_tables()
    load_vault_key()  # ensure vault is ready
    encrypted = _encrypt(body.value)
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            """INSERT INTO env_secrets(name, value, description, updated_at)
               VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ON CONFLICT(name) DO UPDATE SET
                 value=excluded.value, description=excluded.description,
                 updated_at=excluded.updated_at""",
            (name, encrypted, body.description),
        )
        await db.commit()
    return {"name": name, "saved": True}


@router.delete("/secrets/{name}")
async def delete_secret_env(name: str):
    await _ensure_tables()
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute("DELETE FROM env_secrets WHERE name=?", (name,))
        await db.commit()
    return {"deleted": name}


# ── Internal helper used by quick_actions and pipeline_engine ─────────────────

async def load_env_dict() -> dict[str, str]:
    """Return {name: value} for all variables + decrypted secrets. Used to inject into skill subprocesses."""
    await _ensure_tables()
    result: dict[str, str] = {}
    async with aiosqlite.connect(get_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT name, value FROM env_variables") as cur:
            for r in await cur.fetchall():
                result[r["name"]] = r["value"]
        async with db.execute("SELECT name, value FROM env_secrets") as cur:
            for r in await cur.fetchall():
                try:
                    result[r["name"]] = _decrypt(r["value"])
                except Exception:
                    pass
    return result


# ── Files ─────────────────────────────────────────────────────────────────────

def _files_dir() -> Path:
    d = _ENV_FILES_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_file(name: str) -> Path:
    p = (_files_dir() / name).resolve()
    if not str(p).startswith(str(_files_dir().resolve())):
        raise HTTPException(400, "Invalid filename")
    return p


@router.get("/files")
async def list_files():
    d = _files_dir()
    files = []
    for f in sorted(d.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "updated_at": __import__("datetime").datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
    return files


@router.get("/files/{name}")
async def get_file(name: str):
    p = _safe_file(name)
    if not p.exists():
        raise HTTPException(404, f"File '{name}' not found")
    return {"name": name, "content": p.read_text(errors="replace"), "size": p.stat().st_size}


@router.post("/files/upload", status_code=201)
async def upload_file(file: UploadFile = File(...)):
    name = file.filename or "uploaded"
    p = _safe_file(name)
    data = await file.read()
    p.write_bytes(data)
    return {"name": name, "size": len(data)}


@router.delete("/files/{name}")
async def delete_file(name: str):
    p = _safe_file(name)
    if not p.exists():
        raise HTTPException(404, f"File '{name}' not found")
    p.unlink()
    return {"deleted": name}


@router.get("/files/{name}/download")
async def download_file(name: str):
    p = _safe_file(name)
    if not p.exists():
        raise HTTPException(404, f"File '{name}' not found")
    return StreamingResponse(
        io.BytesIO(p.read_bytes()),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
