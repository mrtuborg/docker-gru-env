"""Auth router — top-level OAuth callback endpoints.

These endpoints live outside /api/plugins/ because they are redirect targets
from external OAuth providers (GitHub, Azure AD) and need stable, predictable URLs.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..runtime import server_url

router = APIRouter()
logger = logging.getLogger(__name__)

# Pending manifest registrations: state → plugin_id
_manifest_states: dict[str, str] = {}


def register_manifest_state(state: str, plugin_id: str) -> None:
    """Called from plugins_api to register a pending manifest flow."""
    _manifest_states[state] = plugin_id


@router.get("/github/manifest-callback")
async def manifest_callback(code: str, state: str, request: Request):
    """
    GitHub redirects here after the user creates the app via manifest flow.
    Exchange the temporary code for app credentials, then redirect back to the UI.
    """
    plugin_id = _manifest_states.pop(state, None)
    if not plugin_id:
        raise HTTPException(400, "Invalid or expired state parameter")

    pm = request.app.state.plugins
    plugin = pm.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")

    try:
        app_info = await plugin.complete_manifest_flow(code)
    except Exception as exc:
        logger.exception("Manifest exchange failed for %s", plugin_id)
        # Redirect back with error
        return RedirectResponse(
            f"{server_url()}/#/auth-callback?plugin={plugin_id}&status=error"
            f"&message={str(exc)}"
        )

    # Success — redirect to UI which will guide user to enable device flow + start it
    app_name = app_info.get("app_name", "Gru")
    app_id = app_info.get("app_id", "")
    host = plugin._config.get("host", "github.com")
    html_url = app_info.get("html_url", "")
    import urllib.parse
    return RedirectResponse(
        f"{server_url()}/#/auth-callback"
        f"?plugin={plugin_id}&status=app_registered&app_name={urllib.parse.quote(app_name)}"
        f"&app_id={app_id}&host={host}&settings_url={urllib.parse.quote(html_url)}"
    )
