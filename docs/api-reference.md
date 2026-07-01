# API Reference

Base URL: `http://localhost:9400` (or your container's host:port)

All endpoints return JSON. Authentication is handled via connector tokens stored in the vault — no bearer token required for the API itself (the server is single-tenant).

---

## Connectors (`/api/plugins`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins` | List all installed connectors |
| `GET` | `/api/plugins/{id}` | Get connector config + health |
| `POST` | `/api/plugins` | Install a new connector |
| `PUT` | `/api/plugins/{id}` | Update connector config |
| `DELETE` | `/api/plugins/{id}` | Remove connector |
| `GET` | `/api/plugins/{id}/health` | Current health status |

---

## Dashboard (`/api/dashboard`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard` | Summary: connectors health, pipeline statuses, recent runs |

---

## Pipelines (`/api/pipelines`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pipelines` | List pipelines |
| `POST` | `/api/pipelines` | Create pipeline |
| `GET` | `/api/pipelines/{id}` | Get pipeline with stages |
| `PUT` | `/api/pipelines/{id}` | Update pipeline |
| `DELETE` | `/api/pipelines/{id}` | Delete pipeline |
| `POST` | `/api/pipelines/{id}/start` | Start engine |
| `POST` | `/api/pipelines/{id}/stop` | Stop engine |
| `GET` | `/api/pipelines/{id}/status` | Engine running/stopped |
| `GET` | `/api/pipelines/{id}/log` | SSE log stream |
| `GET` | `/api/pipelines/{id}/runs` | Run history |
| `GET` | `/api/pipelines/{id}/runs/{run_id}` | Single run detail + items |
| `POST` | `/api/pipelines/{id}/import` | Import from YAML body |
| `GET` | `/api/pipelines/{id}/export` | Export as YAML |

---

## Boards (`/api/boards`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/boards` | List all project boards from connected connectors |
| `GET` | `/api/boards/{id}/columns` | Board column names |
| `GET` | `/api/boards/{id}/issues` | Issues grouped by column |

---

## Agents (`/api/agents`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agents` | List agents |
| `POST` | `/api/agents` | Create agent |
| `GET` | `/api/agents/{id}` | Get agent |
| `PUT` | `/api/agents/{id}` | Update agent |
| `DELETE` | `/api/agents/{id}` | Delete agent |
| `POST` | `/api/agents/import/file` | Upload `.agent.md` → parse + save |
| `GET` | `/api/agents/{id}/export` | Download `.agent.md` |

---

## Skills (`/api/skills`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills` | List skills (both locations) |
| `GET` | `/api/skills/{id}` | Skill metadata + file list |
| `POST` | `/api/skills` | Create empty skill |
| `DELETE` | `/api/skills/{id}` | Delete skill (writable only) |
| `GET` | `/api/skills/{id}/files/{name}` | Read file content |
| `PUT` | `/api/skills/{id}/files/{name}` | Write file (copies to writable first) |
| `GET` | `/api/skills/{id}/export` | Download as zip |
| `POST` | `/api/skills/import/zip` | Upload zip to create/update skill |
| `POST` | `/api/skills/{id}/files/upload` | Upload single file into skill |
| `POST` | `/api/skills/sync/workspace` | Copy workspace skills to writable |

---

## Quick Actions (`/api/quick-actions`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/quick-actions` | List quick actions |
| `POST` | `/api/quick-actions` | Create quick action |
| `PUT` | `/api/quick-actions/{id}` | Update quick action |
| `DELETE` | `/api/quick-actions/{id}` | Delete quick action |
| `POST` | `/api/quick-actions/{id}/generate` | Generate issue body (skill or LLM) |
| `POST` | `/api/quick-actions/publish` | Create issue + add to board |

---

## Environment (`/api/env`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/env/variables` | List variables |
| `PUT` | `/api/env/variables/{name}` | Upsert variable |
| `DELETE` | `/api/env/variables/{name}` | Delete variable |
| `GET` | `/api/env/secrets` | List secret names (no values) |
| `PUT` | `/api/env/secrets/{name}` | Upsert secret (encrypted) |
| `DELETE` | `/api/env/secrets/{name}` | Delete secret |
| `GET` | `/api/env/files` | List uploaded files |
| `GET` | `/api/env/files/{name}` | Get file content (JSON with `content` field) |
| `POST` | `/api/env/files/upload` | Upload file |
| `GET` | `/api/env/files/{name}/download` | Download file (binary) |
| `DELETE` | `/api/env/files/{name}` | Delete file |

---

## Sessions (`/api/sessions`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sessions` | List recent Copilot sessions |
| `GET` | `/api/sessions/{id}` | Session detail + cost |

---

## Settings (`/api/settings`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | All settings as key-value |
| `PUT` | `/api/settings/{key}` | Update a setting |

---

## Auth (`/api/auth`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/github/device` | Start GitHub device flow |
| `GET` | `/api/auth/github/callback` | OAuth callback (redirect URI) |
| `GET` | `/api/auth/status` | Current auth state |

---

## SSE events format

The pipeline log stream (`GET /api/pipelines/{id}/log`) emits newline-delimited JSON:

```
data: {"level":"info","message":"Starting pipeline hil-stress","pipeline_id":"hil-stress","timestamp":"2026-07-01T12:00:00Z"}
data: {"level":"info","message":"Picked issue #42 in HW-Check","issue":42,"stage":"HW-Check"}
data: {"level":"success","message":"Session complete — stage advanced","issue":42,"stage":"HW-Update"}
data: {"level":"error","message":"Session timed out after 4h","issue":42}
data: {"level":"warn","message":"Model switch: claude-sonnet-4.6 → claude-haiku-4.5"}
```

Fields: `level` (info/warn/error/success), `message`, `pipeline_id`, `issue`, `stage`, `timestamp`
