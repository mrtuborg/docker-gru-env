"""Settings router — server configuration and YAML import/export."""
from __future__ import annotations

import os

import yaml
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

router = APIRouter()


@router.get("")
async def get_settings(request: Request):
    from ..config import get_all_settings
    settings = await get_all_settings()
    return {
        "data_dir":     os.environ.get("GRU_DATA_DIR", str(__import__("pathlib").Path.home() / ".gru")),
        "server_port":  settings.get("server_port", "9400"),
        "log_level":    settings.get("log_level", "info"),
        "wizard_completed": settings.get("wizard_completed", "false"),
    }


class UpdateSettingsRequest(BaseModel):
    settings: dict


@router.put("")
async def update_settings(body: UpdateSettingsRequest, request: Request):
    from ..config import set_setting
    for key, value in body.settings.items():
        await set_setting(key, str(value))
    return {"ok": True}


@router.get("/export")
async def export_config(request: Request):
    """Export current plugin config as .gru/config.yml format."""
    pm = request.app.state.connectors
    github_connectors = pm.get_by_type("github")
    if not github_connectors:
        raise HTTPException(400, "No GitHub plugin configured — nothing to export")
    plugin = github_connectors[0]
    cfg = plugin._config
    data = {
        "gh_host":   cfg.get("host", "github.com"),
        "data_repo": cfg.get("data_repo", ""),
        "project": {
            "owner":  cfg.get("project_owner", ""),
            "number": cfg.get("project_number", 0),
        },
    }
    if cfg.get("pages_repo"):
        data["pages_repo"] = cfg["pages_repo"]
    if cfg.get("repo_aliases"):
        data["repo_aliases"] = cfg["repo_aliases"]
    if cfg.get("repo_projects"):
        data["repo_projects"] = cfg["repo_projects"]
    watcher = {}
    for k in ("max_issues", "pause_between_sessions", "poll_interval", "stage_order",
              "prompts_dir", "model", "models"):
        if cfg.get(k):
            watcher[k] = cfg[k]
    if watcher:
        data["watcher"] = watcher

    return {"yaml": yaml.safe_dump(data, default_flow_style=False)}


@router.post("/import")
async def import_config(request: Request):
    """Import .gru/config.yml into the server DB as a GitHub plugin."""
    body = await request.json()
    raw = body.get("yaml", "")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"Invalid YAML: {exc}")

    pm = request.app.state.connectors
    config = {
        "host":            data.get("gh_host", "github.com"),
        "data_repo":       data.get("data_repo", ""),
        "pages_repo":      data.get("pages_repo", ""),
        "pages_branch":    (data.get("pages") or {}).get("branch", "main"),
        "project_owner":   (data.get("project") or {}).get("owner", ""),
        "project_number":  (data.get("project") or {}).get("number", 0),
        "project_name":    (data.get("project") or {}).get("name", ""),
        "allowed_repos":   data.get("allowed_repos", []),
        "repo_aliases":    data.get("repo_aliases", {}),
        "repo_projects":   data.get("repo_projects", {}),
    }
    watcher = data.get("watcher") or {}
    config.update({
        "max_issues":             watcher.get("max_issues", 50),
        "pause_between_sessions": watcher.get("pause_between_sessions", 0),
        "poll_interval":          watcher.get("poll_interval", 300),
        "stage_order":            watcher.get("stage_order", ["Todo", "In Progress"]),
        "prompts_dir":            watcher.get("prompts_dir", ""),
        "model":                  watcher.get("model", ""),
        "models":                 watcher.get("models", []),
    })
    host = config["host"].replace(".", "-")
    plugin_id = f"github-{host}"
    try:
        plugin = await pm.add_plugin(plugin_id, "github", config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "ok": True,
        "plugin_id": plugin_id,
        "message": f"Imported as plugin '{plugin_id}'. Add your GitHub token via the Plugins page.",
    }
