"""Sessions & cost router — wraps existing cost-*.py scripts."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_SRC = Path(__file__).parents[2] / "src"


def _run_cost_script(script: str, *args) -> str:
    result = subprocess.run(
        [sys.executable, str(_SRC / script), *args],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{script} failed")
    return result.stdout


@router.get("")
async def list_sessions(request: Request, limit: int = 50):
    from ..config import list_watcher_runs
    runs = await list_watcher_runs(limit=limit)
    return runs


@router.get("/cost/report")
async def cost_report(request: Request, format: str = "json"):
    """Generate a cost report using cost-report.py."""
    pm = request.app.state.plugins
    github_plugins = pm.get_by_type("github")
    if not github_plugins:
        raise HTTPException(400, "No GitHub plugin configured")
    plugin = github_plugins[0]
    config_flag = _build_config_flag(plugin)
    try:
        if format == "json":
            out = _run_cost_script("cost-report.py", *config_flag, "--format", "json")
            return json.loads(out) if out.strip() else []
        else:
            out = _run_cost_script("cost-report.py", *config_flag)
            return {"text": out}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/cost/sync")
async def cost_sync(request: Request):
    """Trigger cost-link + board-sync."""
    pm = request.app.state.plugins
    github_plugins = pm.get_by_type("github")
    if not github_plugins:
        raise HTTPException(400, "No GitHub plugin configured")
    plugin = github_plugins[0]
    config_flag = _build_config_flag(plugin)
    try:
        out = _run_cost_script("cost-link.py", *config_flag, "--apply")
        return {"ok": True, "output": out}
    except Exception as exc:
        raise HTTPException(500, str(exc))


def _build_config_flag(plugin) -> list[str]:
    """Write a temporary config.yml for the plugin and return --config flag."""
    import tempfile
    import yaml
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

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
    yaml.safe_dump(data, tmp)
    tmp.close()
    return ["--config", tmp.name]
