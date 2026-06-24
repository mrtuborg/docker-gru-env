"""Dashboard router — aggregated status for the home page."""
from __future__ import annotations

from fastapi import APIRouter, Request
from ..plugin_base import HealthStatus

router = APIRouter()


@router.get("")
async def dashboard(request: Request):
    pm = request.app.state.plugins
    plugins_summary = []
    for plugin in pm.get_all():
        health = pm.get_health(plugin.plugin_id)
        plugins_summary.append({
            "id":           plugin.plugin_id,
            "plugin_type":  plugin.plugin_type,
            "display_name": plugin.display_name,
            "icon":         plugin.icon,
            "health":       {"status": health.status, "message": health.message},
        })

    overall = HealthStatus.HEALTHY
    if any(p["health"]["status"] == HealthStatus.ERROR for p in plugins_summary):
        overall = HealthStatus.ERROR
    elif any(p["health"]["status"] == HealthStatus.DEGRADED for p in plugins_summary):
        overall = HealthStatus.DEGRADED

    return {
        "overall_health": overall,
        "plugins": plugins_summary,
        "needs_setup": pm.needs_setup(),
    }
