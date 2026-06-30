"""Boards router — GitHub project boards + Obsidian kanban boards."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..vault import load_secret

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


async def _gh_board_columns(plugin, owner: str, number: int) -> list[dict]:
    """Fetch GitHub Projects v2 status columns via GraphQL."""
    plugin_id = plugin.plugin_id
    token = await load_secret(plugin_id, "token")
    if not token:
        raise HTTPException(401, "Connector has no token — authorize first")

    host = plugin._config.get("host", "github.com")
    gql_url = (
        f"https://{host}/api/graphql"
        if host != "github.com"
        else "https://api.github.com/graphql"
    )

    # Detect org vs user
    check_url = f"https://{host}/api/v3/orgs/{owner}" if host != "github.com" else f"https://api.github.com/orgs/{owner}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(check_url, headers={"Authorization": f"Bearer {token}"})
        entity = "organization" if r.status_code == 200 else "user"

    query = """
    query($owner: String!, $number: Int!) {
      %s(login: $owner) {
        projectV2(number: $number) {
          title
          field(name: "Status") {
            ... on ProjectV2SingleSelectField {
              options { name }
            }
          }
        }
      }
    }
    """ % entity

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            gql_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": query, "variables": {"owner": owner, "number": number}},
        )

    data = resp.json()
    node = data.get("data", {}).get(entity, {}).get("projectV2") or {}
    options = (node.get("field") or {}).get("options", [])
    return [{"name": opt["name"], "card_count": 0, "cards": []} for opt in options]


@router.get("/{board_id:path}/columns")
async def board_columns(board_id: str, request: Request):
    """Return columns for a board (GitHub Projects v2 or Obsidian kanban)."""
    plugin_id = board_id.replace("/board", "")
    pm = request.app.state.connectors
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, "Board not found")

    if plugin.connector_type == "obsidian":
        columns = plugin.list_columns()
        return [{"name": col, "card_count": len(plugin.list_cards(col)), "cards": plugin.list_cards(col)} for col in columns]

    if plugin.connector_type == "github":
        owner = plugin._config.get("project_owner", "")
        number = plugin._config.get("project_number", 0)
        if not owner or not number:
            raise HTTPException(400, "GitHub connector has no project_owner/project_number configured")
        return await _gh_board_columns(plugin, owner, number)

    raise HTTPException(400, f"Column listing not supported for {plugin.connector_type} boards")
