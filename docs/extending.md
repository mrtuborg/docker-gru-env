# Extending Gru Server

This document explains how to extend the system in the most common ways, while preserving maintainability.

## Adding a new connector

See [connectors.md](connectors.md#adding-a-new-connector) for the full walkthrough. Summary:

1. Create `server/connectors/my_connector.py` implementing `GruConnector`
2. Register in `connector_manager.py`
3. Redeploy: `docker cp server/connectors/my_connector.py gru-server-dev:/app/server/connectors/ && docker restart gru-server-dev`

The wizard UI renders the config form automatically from `config_schema()`.

---

## Adding a new API router

1. Create `server/routers/my_router.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("")
async def list_things():
    return []
```

2. Register in `server/app.py`:
```python
from .routers import ..., my_router
app.include_router(my_router.router, prefix="/api/my-things", tags=["my-things"])
```

3. Redeploy backend files + restart.

---

## Adding a new database table

1. Add DDL to `server/config.py`:
```python
DDL = """
...
CREATE TABLE IF NOT EXISTS my_table (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
"""
```

2. If modifying an existing table, add an `ALTER TABLE` to the migrations list:
```python
migrations = [
    ...
    "ALTER TABLE my_table ADD COLUMN new_col TEXT DEFAULT ''",
]
```

3. Restart the server — `init_db()` runs DDL + migrations at startup.

---

## Adding a new skill

1. Create folder `skills/my-skill/` in the workspace repo or upload via Skills UI
2. Add `SKILL.md` (description line = first non-header text)
3. Add `run.sh "$title" "$context"` for Quick Actions Generate
4. (Optional) Add `create.sh "$title" "$context"` for Quick Actions Publish
5. The skill appears in Quick Actions dropdown immediately (no restart)

See [skills.md](skills.md) for the full interface spec.

---

## Adding a new page to the frontend

1. Create `web/src/pages/MyPage.tsx`:
```tsx
export default function MyPage() {
  return <div style={{ padding: 24 }}>Hello</div>
}
```

2. Import and register in `web/src/App.tsx`:
```tsx
import MyPage from './pages/MyPage'
// Add icon import
import { ..., MyIcon } from 'lucide-react'

// Add nav link (inside sidebar):
<NavLink to="/my-page" className={...}><MyIcon size={16}/>My Page</NavLink>

// Add route (inside <Routes>):
<Route path="/my-page" element={<MyPage />} />
```

3. Hot-deploy:
```bash
npm --prefix web run build && docker cp server/static/. gru-server-dev:/app/server/static/
```

---

## Adding a pipeline stage type

Currently stages are either `ai` or `human`. To add a new actor type:

1. Extend `pipeline_stages.actor` to accept new value (schema change optional — SQLite stores any string)
2. Update `pipeline_engine.py:_should_process_stage()` to handle the new type
3. Update `PipelineEditor.tsx` to render the new actor in Blueprint + Edit mode

---

## Adding a new Quick Action type

Currently `action_type = 'create_issue'` is the only type. To add e.g. `run_script`:

1. Add handling in `quick_actions.py` generate/publish endpoints
2. Update the Quick Action editor in `Boards.tsx` to show type-specific fields
3. The `config_json` field is free-form — add any new fields needed

---

## Extending the agent format

Agent files use YAML frontmatter + markdown body. To add new frontmatter fields:

1. Add to `parse_agent_md()` in `server/routers/agents.py`
2. Add to `build_agent_md()` for export
3. Add column to `agents` table (migration)
4. Update `AgentCreate`/`AgentUpdate` Pydantic models
5. Update `Agents.tsx` to show/edit the new field

---

## Maintainability guidelines

### Python backend

- **One router per domain** — don't add unrelated endpoints to an existing router
- **All DB access via `config.py`** — never write raw SQL in routers; add a function to `config.py`
- **Async everywhere** — all DB and HTTP calls must be `async`; never use `time.sleep()` in routers
- **Secrets via vault** — never store secrets in `plugins.config` JSON; always use `store_secret()` / `load_secret()`
- **Migrations are additive** — only `ALTER TABLE ADD COLUMN`; never drop columns

### Frontend

- **No CSS framework** — use CSS custom properties from `index.css` directly on style attributes
- **No state management library** — `useState` + `useCallback` is sufficient for this app's complexity
- **Inline editing over modals** — prefer expanding a row in-place over a modal dialog
- **All API calls to `/api/...`** — never hardcode `localhost:9400`; the path prefix is sufficient
- **TypeScript interfaces match API** — keep Pydantic models and TS interfaces in sync

### Skills

- **Pure stdout** — skill scripts write only the output body to stdout; logs/progress go to stderr
- **No side effects in run.sh** — Generate must be safe to call repeatedly; Publish (create.sh) does the write
- **WORKSPACE env var** — never hardcode `/workspace`; read `${WORKSPACE:-/workspace}`
- **Fail loudly** — `set -euo pipefail` at the top of every script
- **Check env vars early** — verify `GH_TOKEN`, `GH_HOST` exist before making API calls

### Container

- **Data volume** — all persistent state lives in `/data/gru/`; nothing in the image layers
- **Hot-deploy first** — prefer `docker cp` + `docker restart` over `docker build`; rebuild only when `Dockerfile.server` changes
- **One process** — the container runs only uvicorn; `gh copilot session` is a subprocess, not a daemon
