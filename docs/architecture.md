# Architecture

## Overview

Gru Server is a single Docker container that provides:

- A **FastAPI** backend serving REST + SSE APIs
- A **React SPA** (Vite, TypeScript) served from `/static/`
- A **SQLite database** at `/data/gru/server.db` (WAL mode)
- A **Pipeline Engine** that polls GitHub Project boards and launches `gh copilot session` subprocesses
- A **Vault** for AES-256-GCM encrypted secret storage
- A **Connector system** for pluggable integrations (GitHub, Copilot, Azure, Obsidian)

## Component map

```
┌─────────────────────────────────────────────────────────┐
│  Docker container: gru-server-dev (port 9400)           │
│                                                         │
│  ┌──────────┐    ┌─────────────────────────────────┐   │
│  │  React   │    │  FastAPI (Uvicorn)               │   │
│  │  SPA     │◄──►│  /api/*  routers                 │   │
│  │ (static) │    │  SSE streams                     │   │
│  └──────────┘    └──────┬──────────────────────┬────┘   │
│                         │                      │        │
│               ┌─────────▼──────┐   ┌───────────▼─────┐ │
│               │ Pipeline       │   │ ConnectorManager │ │
│               │ Engine         │   │                  │ │
│               │ (asyncio loop) │   │ GitHub connector │ │
│               └─────────┬──────┘   │ Copilot conn.   │ │
│                         │          │ Azure connector  │ │
│               ┌─────────▼──────┐   │ Obsidian conn.  │ │
│               │ gh copilot     │   └─────────────────┘ │
│               │ session (subprocess)                    │
│               └────────────────┘                       │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  SQLite DB  /data/gru/server.db                 │   │
│  │  + Vault key /data/gru/vault.key                │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  Volumes:                                               │
│    /data          ← persistent DB + vault + env files   │
│    /workspace     ← host repo mount (read-only)         │
└─────────────────────────────────────────────────────────┘
```

## Directory structure

```
docker-gru-env/
├── server/                     # Python FastAPI backend
│   ├── app.py                  # Application factory + lifespan
│   ├── config.py               # SQLite schema + all async DB functions
│   ├── vault.py                # AES-256-GCM secret store
│   ├── connector_base.py       # GruConnector ABC
│   ├── connector_manager.py    # Load/reload/teardown connectors
│   ├── runtime.py              # Server entry point (uvicorn)
│   ├── connectors/             # Connector implementations
│   │   ├── copilot_connector.py
│   │   ├── copilot_connector.py  (GitHub PAT / OAuth)
│   │   ├── azure_connector.py
│   │   └── obsidian_connector.py
│   ├── models/                 # Pydantic request/response models
│   │   └── pipeline.py
│   ├── routers/                # FastAPI route handlers (one per domain)
│   │   ├── agents.py           # /api/agents
│   │   ├── boards.py           # /api/boards
│   │   ├── connectors_api.py   # /api/plugins
│   │   ├── dashboard.py        # /api/dashboard
│   │   ├── environment.py      # /api/env
│   │   ├── pipelines.py        # /api/pipelines
│   │   ├── quick_actions.py    # /api/quick-actions
│   │   ├── sessions.py         # /api/sessions
│   │   ├── settings_api.py     # /api/settings
│   │   ├── skills.py           # /api/skills
│   │   └── wizard.py           # /api/wizard
│   ├── services/
│   │   └── pipeline_engine.py  # Core orchestration engine
│   └── static/                 # Built React SPA (generated)
│
├── web/                        # React + TypeScript frontend
│   ├── src/
│   │   ├── App.tsx             # Router + nav sidebar
│   │   ├── index.css           # Design tokens + global styles
│   │   └── pages/              # One file per page
│
├── docs/                       # This documentation
├── skills/                     # Copilot CLI skills (server-side)
├── Dockerfile.server           # Container image definition
├── docker-compose.server.yml   # Compose file for gru-server-dev
├── server-run.sh               # Start/restart script
└── server-build.sh             # Build image script
```

## Request lifecycle

```
Browser → GET /api/pipelines
       → FastAPI router (pipelines.py)
       → config.py (async SQLite query via aiosqlite)
       → JSON response

Browser → POST /api/quick-actions/generate
       → quick_actions.py
       → loads env vars from environment.py:load_env_dict()
       → finds skill folder (/workspace/skills/ or ~/.copilot/skills/)
       → asyncio.create_subprocess_exec("bash", "run.sh", title, context)
       → stdout → JSON response {"body": "...", "source": "skill"}

Browser → POST /api/pipelines/{id}/start
       → pipelines.py
       → PipelineEngine.start(pipeline_id)
       → async loop: GraphQL poll → pick issue → render prompt → subprocess
       → SSE events: GET /api/pipelines/{id}/log
```

## Data persistence

| Data | Location | Backed by |
|------|----------|-----------|
| All config (pipelines, agents, settings) | `/data/gru/server.db` | SQLite WAL |
| Encrypted secrets (connector PATs, env secrets) | `/data/gru/server.db` `credentials` + `env_secrets` tables | Vault (AES-256-GCM) |
| Vault key | `/data/gru/vault.key` | File (256-bit random, base64) |
| Environment files | `/data/gru/env/files/` | Container filesystem |
| Pipeline run history | `/data/gru/server.db` `pipeline_runs` + `pipeline_run_items` | SQLite |

> **Backup**: copy the entire `/data/gru/` directory. The vault key is required to decrypt secrets — back it up separately.

## Network

The container exposes **port 9400** (configurable in `docker-compose.server.yml`).
- No TLS termination inside the container — put nginx/Traefik in front for production.
- The React SPA makes all API calls to the same origin (`/api/...`), no CORS issues in production.
- CORS is enabled for `localhost:5173` (Vite dev server) to support frontend development without a container restart.

## Startup sequence

1. `docker-entrypoint.sh` → starts uvicorn
2. `app.py:lifespan()`:
   - `init_db()` — runs DDL + migrations (idempotent, safe to re-run)
   - `ConnectorManager.load_all()` — instantiates all enabled plugins from DB, calls `configure()`
   - `PipelineEngine()` — created but idle; starts only when `POST /api/pipelines/{id}/start` is called
3. FastAPI serves requests
4. On shutdown: `engine.stop_all()` → `connector_manager.teardown_all()`
