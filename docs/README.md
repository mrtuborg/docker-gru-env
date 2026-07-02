# Gru Server — Documentation Index

> **Gru's Lab**: a self-hosted pipeline orchestration server that drives AI agents (GitHub Copilot CLI) through GitHub Projects v2 boards, with a full React management UI.

## Documents

| Document | What it covers |
|----------|---------------|
| [architecture.md](architecture.md) | System overview, component map, data flow, deployment |
| [connectors.md](connectors.md) | Connector system — how to use existing connectors and add new ones |
| [pipelines.md](pipelines.md) | Pipeline engine, stages, agents, models, state machine |
| [agents.md](agents.md) | Agent library, `.agent.md` format, orchestrators |
| [skills.md](skills.md) | Skill system, script conventions, Quick Actions integration |
| [environment.md](environment.md) | Environment page — variables, secrets, files |
| [quick-actions.md](quick-actions.md) | Quick Actions panel — CRUD, generate, publish, skill-linked |
| [api-reference.md](api-reference.md) | All REST endpoints with request/response shapes |
| [database-schema.md](database-schema.md) | SQLite schema, all tables, vault encryption |
| [frontend.md](frontend.md) | React app structure, pages, routing, theming |
| [extending.md](extending.md) | How to extend: new connector, new skill, new pipeline type |
| [operations.md](operations.md) | Deployment, hot-deploy, container management, backup |

## Quick start

```bash
# Start the server
./server-run.sh

# Open the UI
open http://localhost:9400

# Hot-deploy frontend changes (no restart)
npm --prefix web run build && docker cp server/static/. gru-server-dev:/app/server/static/

# Hot-deploy backend changes (restart required)
docker cp server/routers/pipelines.py gru-server-dev:/app/server/routers/pipelines.py
docker restart gru-server-dev
```

## Design principles

1. **Pull principle** — the pipeline engine always picks up the rightmost AI-actionable issue on the board. Work advances left→right; the engine always works backwards to find the most advanced issue.
2. **Connector-first** — every GitHub/AI operation goes through a named connector that owns auth (PAT or OAuth). Secrets are AES-256-GCM encrypted at rest.
3. **Skills over scripts** — reusable capabilities live in `skills/<name>/` folders with a standard interface (`run.sh`, `create.sh`, `SKILL.md`). Skills are injected with env vars from the Environment page.
4. **Observable by default** — every pipeline run emits SSE log events, stores run records, and tracks per-issue attempt counts and costs.
5. **Hot-deployable** — Python and frontend changes do not require a container rebuild. Only `Dockerfile.server` changes need `server-build.sh`.
