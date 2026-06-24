"""Plugins router — CRUD, health, OAuth flows."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class CreatePluginRequest(BaseModel):
    id:          str
    plugin_type: str
    config:      dict = {}


class UpdatePluginRequest(BaseModel):
    config: dict


class StorePATRequest(BaseModel):
    token: str


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
    # Update cache
    pm._health_cache[plugin_id] = health
    return {"status": health.status, "message": health.message, "details": health.details}


@router.get("/{plugin_id}/schema")
async def plugin_schema(plugin_id: str, request: Request):
    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return plugin.config_schema()


# ── OAuth ─────────────────────────────────────────────────────────────────────

# Pending device flows: plugin_id → {device_code, interval, task}
_pending_flows: dict[str, dict] = {}


@router.post("/{plugin_id}/auth/device/start")
async def start_device_flow(plugin_id: str, request: Request):
    """Start OAuth device flow. Returns user_code + verification_uri."""
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
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/{plugin_id}/auth/device/poll")
async def poll_device_flow(plugin_id: str, request: Request):
    """Poll for OAuth token. Returns {granted: bool, message: str}."""
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


@router.get("/{plugin_id}/credentials")
async def list_credentials(plugin_id: str, request: Request):
    """List credential keys (no values) for a plugin."""
    from ..vault import list_secret_keys
    pm = request.app.state.plugins
    if not pm.get(plugin_id):
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    return await list_secret_keys(plugin_id)
