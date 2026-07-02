"""Analytics router — cross-pipeline overview and per-run detail, backed by
the AnalyticsConnector (PostgreSQL) via IAnalyticsStore."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..connectors.analytics_connector import IAnalyticsStore

router = APIRouter()


def _get_analytics_store(request: Request) -> IAnalyticsStore | None:
    """Return the first configured analytics connector, or None."""
    connectors = request.app.state.connectors.get_by_type("analytics")
    for conn in connectors:
        if isinstance(conn, IAnalyticsStore):
            return conn
    return None


@router.get("/overview")
async def overview(request: Request, days: int = 90):
    store = _get_analytics_store(request)
    if store is None:
        raise HTTPException(400, "No analytics connector configured")
    data = await store.read_overview(days=days)
    if data.get("analytics_unavailable"):
        raise HTTPException(503, "Analytics database unavailable")
    return data


@router.get("/runs/{run_id}")
async def run_detail(request: Request, run_id: str):
    store = _get_analytics_store(request)
    if store is None:
        raise HTTPException(400, "No analytics connector configured")
    data = await store.read_run_detail(run_id)
    if data.get("analytics_unavailable"):
        raise HTTPException(503, "Analytics database unavailable")
    if data.get("error"):
        raise HTTPException(404, data["error"])
    return data
