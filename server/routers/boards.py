"""Boards router — GitHub project boards + Obsidian kanban boards."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class WatcherAction(BaseModel):
    action: str  # "start" | "stop"


@router.get("")
async def list_boards(request: Request):
    pm = request.app.state.connectors
    boards = []
    for plugin in pm.get_by_type("github"):
        boards.append({
            "id":     f"{plugin.plugin_id}/board",
            "type":   "github",
            "name":   plugin._config.get("project_name") or f"Board #{plugin._config.get('project_number')}",
            "plugin": plugin.plugin_id,
            "config": {
                "project_number": plugin._config.get("project_number"),
                "project_owner":  plugin._config.get("project_owner"),
                "host":           plugin._config.get("host"),
            },
        })
    for plugin in pm.get_by_type("obsidian"):
        boards.append({
            "id":     f"{plugin.plugin_id}/board",
            "type":   "obsidian",
            "name":   plugin.display_name,
            "plugin": plugin.plugin_id,
            "config": {"board_path": plugin._config.get("board_path")},
        })
    return boards


@router.get("/{board_id:path}/columns")
async def board_columns(board_id: str, request: Request):
    """Return columns + card counts for an Obsidian board."""
    plugin_id = board_id.replace("/board", "")
    pm = request.app.state.connectors
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, "Board not found")
    if plugin.connector_type != "obsidian":
        raise HTTPException(400, "Column listing is only supported for Obsidian boards")
    columns = plugin.list_columns()
    result = []
    for col in columns:
        cards = plugin.list_cards(col)
        result.append({"name": col, "card_count": len(cards), "cards": cards})
    return result
