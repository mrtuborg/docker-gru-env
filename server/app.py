"""
Gru Server — FastAPI application factory.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import init_db
from .plugin_manager import PluginManager
from .routers import dashboard, plugins_api, wizard, boards, sessions, settings_api

logger = logging.getLogger(__name__)

# Singleton plugin manager, accessible across routers
plugin_manager: PluginManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop server lifecycle: init DB, load plugins."""
    global plugin_manager
    logger.info("Gru Server starting…")
    await init_db()
    plugin_manager = PluginManager()
    await plugin_manager.load_all()
    app.state.plugins = plugin_manager
    yield
    logger.info("Gru Server shutting down…")
    if plugin_manager:
        await plugin_manager.teardown_all()


def create_app(data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="Gru's Lab Server",
        description="Web UI and API for docker-gru-env",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Store data_dir on app state before lifespan runs
    app.state.data_dir = data_dir or Path.home() / ".gru"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers
    app.include_router(wizard.router,       prefix="/api/wizard",   tags=["wizard"])
    app.include_router(plugins_api.router,  prefix="/api/plugins",  tags=["plugins"])
    app.include_router(dashboard.router,    prefix="/api/dashboard", tags=["dashboard"])
    app.include_router(boards.router,       prefix="/api/boards",   tags=["boards"])
    app.include_router(sessions.router,     prefix="/api/sessions", tags=["sessions"])
    app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])

    # Serve built React SPA from static/ — only in production
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists() and any(static_dir.iterdir()):
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            index = static_dir / "index.html"
            return FileResponse(index)

    return app
