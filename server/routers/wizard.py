"""Wizard router — setup state and wizard completion."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/status")
async def wizard_status(request: Request):
    pm = request.app.state.connectors
    available_types = [
        {"id": "github",   "name": "GitHub",          "icon": "Github",   "description": "Project board watcher, cost reporting, and Copilot attribution"},
        {"id": "copilot",  "name": "GitHub Copilot",  "icon": "Bot",      "description": "Interactive and automated Copilot CLI sessions with cost tracking"},
        {"id": "azure",    "name": "Azure Storage",   "icon": "Cloud",    "description": "Azure Blob Storage access for firmware bundles with auto token refresh"},
        {"id": "obsidian", "name": "Obsidian Kanban", "icon": "FileText", "description": "Watches an Obsidian Kanban board and runs Copilot sessions per card"},
    ]
    return {
        "needs_setup": pm.needs_setup(),
        "plugin_count": len(pm.get_all()),
        "available_types": available_types,
    }


@router.post("/complete")
async def wizard_complete(request: Request):
    """Mark wizard as complete (no-op if plugins already configured)."""
    from ..config import set_setting
    await set_setting("wizard_completed", "true")
    return {"ok": True}
