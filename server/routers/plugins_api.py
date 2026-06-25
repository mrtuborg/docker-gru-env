"""Plugins router — CRUD, health, OAuth flows, GitHub App Manifest registration."""
from __future__ import annotations

import asyncio
import json
import secrets as sec
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .auth import register_manifest_state

router = APIRouter()


class CreatePluginRequest(BaseModel):
    id:          str
    plugin_type: str
    config:      dict = {}


class UpdatePluginRequest(BaseModel):
    config: dict


class StorePATRequest(BaseModel):
    token: str


class StoreSecretRequest(BaseModel):
    key:   str
    value: str


# ── Collection ────────────────────────────────────────────────────────────────

@router.get("")
async def list_plugins(request: Request):
    pm = request.app.state.plugins
    result = []
    for plugin in pm.get_all():
        health = pm.get_health(plugin.plugin_id)
        result.append({**plugin.to_dict(), "health": {"status": health.status, "message": health.message}})
    return result


@router.post("", status_code=201)
async def create_plugin(body: CreatePluginRequest, request: Request):
    pm = request.app.state.plugins
    if pm.get(body.id):
        raise HTTPException(409, f"Plugin '{body.id}' already exists")
    try:
        plugin = await pm.add_plugin(body.id, body.plugin_type, body.config)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return plugin.to_dict()


# ── Individual ────────────────────────────────────────────────────────────────

@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str, request: Request):
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    health = pm.get_health(plugin_id)
    schema = plugin.config_schema()
    return {**plugin.to_dict(), "health": {"status": health.status, "message": health.message}, "schema": schema}


@router.put("/{plugin_id}")
async def update_plugin(plugin_id: str, body: UpdatePluginRequest, request: Request):
    pm = request.app.state.plugins
    try:
        plugin = await pm.update_plugin(plugin_id, body.config)
    except KeyError:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return plugin.to_dict()


@router.delete("/{plugin_id}", status_code=204)
async def delete_plugin(plugin_id: str, request: Request):
    pm = request.app.state.plugins
    if not pm.get(plugin_id):
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    await pm.remove_plugin(plugin_id)


@router.get("/{plugin_id}/health")
async def plugin_health(plugin_id: str, request: Request):
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    health = await plugin.health()
    pm._health_cache[plugin_id] = health
    return {"status": health.status, "message": health.message, "details": health.details}


@router.get("/{plugin_id}/schema")
async def plugin_schema(plugin_id: str, request: Request):
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return plugin.config_schema()


# ── Auth status ───────────────────────────────────────────────────────────────

@router.get("/{plugin_id}/auth/status")
async def auth_status(plugin_id: str, request: Request):
    """
    Return auth readiness for a plugin.
    For GitHub: { has_token, has_client_id, needs_manifest, host }
    For Azure: { has_token, needs_auth }
    """
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")

    if hasattr(plugin, "auth_status"):
        return await plugin.auth_status()

    # Generic: check if a token exists
    from ..vault import load_secret
    token = await load_secret(plugin_id, "token") or await load_secret(plugin_id, "access_token")
    return {"has_token": token is not None}


# ── OAuth Device Flow (GitHub + Azure) ────────────────────────────────────────

_pending_flows: dict[str, dict] = {}


@router.post("/{plugin_id}/auth/device/start")
async def start_device_flow(plugin_id: str, request: Request):
    """Start OAuth device flow for GitHub or Azure. Returns user_code + verification_uri."""
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    if not hasattr(plugin, "start_device_flow"):
        raise HTTPException(400, f"Plugin '{plugin_id}' does not support device flow")
    try:
        flow = await plugin.start_device_flow()
        _pending_flows[plugin_id] = {"device_code": flow["device_code"], "interval": flow.get("interval", 5)}
        return {
            "user_code":        flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "expires_in":       flow.get("expires_in", 900),
            "interval":         flow.get("interval", 5),
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/{plugin_id}/auth/device/poll")
async def poll_device_flow(plugin_id: str, request: Request):
    """Poll for OAuth token (GitHub or Azure). Returns {granted: bool, message: str}."""
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    flow = _pending_flows.get(plugin_id)
    if not flow:
        raise HTTPException(400, "No pending device flow — call /auth/device/start first")
    try:
        token = await plugin.poll_device_flow(flow["device_code"], flow["interval"])
        if token:
            _pending_flows.pop(plugin_id, None)
            return {"granted": True, "message": "Authorization successful"}
        return {"granted": False, "message": "Pending authorization"}
    except RuntimeError as exc:
        _pending_flows.pop(plugin_id, None)
        raise HTTPException(400, str(exc))


@router.post("/{plugin_id}/auth/pat")
async def store_pat(plugin_id: str, body: StorePATRequest, request: Request):
    """Store a Personal Access Token directly."""
    from ..vault import store_secret
    pm = request.app.state.plugins
    if not pm.get(plugin_id):
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    await store_secret(plugin_id, "token", body.token)
    return {"ok": True}


@router.post("/{plugin_id}/auth/secret")
async def store_secret_endpoint(plugin_id: str, body: StoreSecretRequest, request: Request):
    """Store a named secret in the vault (e.g. sas_token, client_secret)."""
    from ..vault import store_secret
    pm = request.app.state.plugins
    if not pm.get(plugin_id):
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    await store_secret(plugin_id, body.key, body.value)
    return {"ok": True}


@router.get("/{plugin_id}/credentials")
async def list_credentials(plugin_id: str, request: Request):
    """List credential keys (no values) for a plugin."""
    from ..vault import list_secret_keys
    pm = request.app.state.plugins
    if not pm.get(plugin_id):
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return await list_secret_keys(plugin_id)


# ── GitHub App Manifest Flow ─────────────────────────────────────────────────

# Pending manifest registrations: state → plugin_id
_manifest_states: dict[str, str] = {}


@router.get("/{plugin_id}/auth/manifest/register")
async def manifest_register(plugin_id: str, request: Request):
    """
    Serve an HTML page that auto-submits the GitHub App manifest form.
    This redirects the user's browser to GitHub to create the OAuth App.
    """
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    if not hasattr(plugin, "get_manifest"):
        raise HTTPException(400, "Plugin does not support manifest registration")

    manifest = plugin.get_manifest()
    host = plugin._config.get("host", "github.com")
    state = sec.token_urlsafe(32)
    register_manifest_state(state, plugin_id)

    manifest_json = json.dumps(manifest)
    action_url = f"https://{host}/settings/apps/new?state={state}"

    html = f"""<!DOCTYPE html>
<html>
<head><title>Registering GitHub App…</title>
<style>
  body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, sans-serif;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 12px;
           padding: 40px; text-align: center; max-width: 420px; }}
  h2 {{ color: #58a6ff; margin-bottom: 12px; }}
  p {{ color: #8b949e; line-height: 1.6; }}
</style>
</head>
<body>
  <div class="card">
    <h2>🔧 Registering GitHub App</h2>
    <p>Redirecting to <strong>{host}</strong> to create the OAuth App…<br>
       Click "Create GitHub App" on the next page.</p>
    <form id="mf" method="post" action="{action_url}">
      <input type="hidden" name="manifest" value='{manifest_json}'>
    </form>
  </div>
  <script>document.getElementById('mf').submit();</script>
</body>
</html>"""
    return HTMLResponse(html)
