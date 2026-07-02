# API Reference

Base URL: `http://localhost:9400` (or your container's host:port)

All endpoints return JSON. Authentication is handled via connector tokens stored in the vault — no bearer token required for the API itself (the server is single-tenant).

---

## Connectors (`/api/plugins`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/plugins/types` | Available connector types |
| `GET` | `/api/plugins` | List all installed connectors |
| `GET` | `/api/plugins/{id}` | Get connector config |
| `POST` | `/api/plugins` | Install a new connector |
| `PUT` | `/api/plugins/{id}` | Update connector config |
| `DELETE` | `/api/plugins/{id}` | Remove connector |
| `GET` | `/api/plugins/{id}/health` | Current health status |
| `GET` | `/api/plugins/{id}/schema` | Config schema for the UI |
| `GET` | `/api/plugins/{id}/auth/status` | Auth status |
| `POST` | `/api/plugins/{id}/auth/device/start` | Start GitHub device flow |
| `POST` | `/api/plugins/{id}/auth/device/poll` | Poll device flow for token |
| `POST` | `/api/plugins/{id}/auth/pat` | Store a Personal Access Token |
| `POST` | `/api/plugins/{id}/auth/secret` | Store an arbitrary secret |
| `GET` | `/api/plugins/{id}/credentials` | List credential keys (no values) |
| `GET` | `/api/plugins/{id}/auth/manifest/register` | GitHub App manifest registration redirect |

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
| `POST` | `/api/pipelines/import` | Import pipeline from YAML body |
| `GET` | `/api/pipelines/{id}` | Get pipeline with stages |
| `PUT` | `/api/pipelines/{id}` | Update pipeline |
| `DELETE` | `/api/pipelines/{id}` | Delete pipeline |
| `POST` | `/api/pipelines/{id}/start` | Start engine |
| `POST` | `/api/pipelines/{id}/stop` | Stop engine |
| `POST` | `/api/pipelines/{id}/run-once` | Run one poll cycle immediately |
| `GET` | `/api/pipelines/{id}/status` | Engine running/stopped + current issue |
| `GET` | `/api/pipelines/{id}/logs` | SSE log stream |
| `GET` | `/api/pipelines/{id}/runs` | Run history |
| `GET` | `/api/pipelines/{id}/runs/{run_id}/items` | Items for a specific run |
| `GET` | `/api/pipelines/{id}/state` | Per-issue state (attempt counts) |
| `DELETE` | `/api/pipelines/{id}/state` | Clear all issue state |
| `GET` | `/api/pipelines/board-columns/{plugin_id}` | Board column names for a connector |

---

## Boards (`/api/boards`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/boards` | List project boards from all connectors |
| `GET` | `/api/boards/{board_id}/columns` | Column names for a board |

---

## Agents (`/api/agents`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agents` | List agents |
| `POST` | `/api/agents` | Create agent |
| `GET` | `/api/agents/{id}` | Get agent |
| `PUT` | `/api/agents/{id}` | Update agent |
| `DELETE` | `/api/agents/{id}` | Delete agent |
| `POST` | `/api/agents/import/file` | Import from file path on disk |
| `POST` | `/api/agents/import/upload` | Upload `.agent.md` file |
| `POST` | `/api/agents/import/repo` | Import from git repo URL |

---

## Skills (`/api/skills`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills` | List skills (both locations) |
| `GET` | `/api/skills/{id}` | Skill metadata + file list |
| `POST` | `/api/skills` | Create empty skill |
| `DELETE` | `/api/skills/{id}` | Delete skill (writable only) |
| `GET` | `/api/skills/{id}/files/{name}` | Read file content |
| `PUT` | `/api/skills/{id}/files/{name}` | Write file |
| `POST` | `/api/skills/{id}/files/upload` | Upload file into skill |
| `GET` | `/api/skills/{id}/export` | Download skill as zip |
| `POST` | `/api/skills/import/zip` | Upload zip to create/update skill |
| `POST` | `/api/skills/sync/workspace` | Copy workspace skills to writable location |

---

## Quick Actions (`/api/quick-actions`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/quick-actions` | List quick actions |
| `POST` | `/api/quick-actions` | Create quick action |
| `GET` | `/api/quick-actions/{id}` | Get quick action |
| `PUT` | `/api/quick-actions/{id}` | Update quick action |
| `DELETE` | `/api/quick-actions/{id}` | Delete quick action |
| `POST` | `/api/quick-actions/generate` | Generate issue body (skill or LLM) |
| `POST` | `/api/quick-actions/publish` | Create issue + add to board |

---

## Environment (`/api/env`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/env/variables` | List variables |
| `PUT` | `/api/env/variables/{name}` | Upsert variable |
| `DELETE` | `/api/env/variables/{name}` | Delete variable |
| `GET` | `/api/env/secrets` | List secret names (values masked) |
| `PUT` | `/api/env/secrets/{name}` | Upsert secret (encrypted at rest) |
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
| `GET` | `/api/sessions/cost/report` | Cost report (aggregated) |
| `POST` | `/api/sessions/cost/sync` | Sync cost data from disk |

---

## Settings (`/api/settings`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | All settings as key-value |
| `PUT` | `/api/settings` | Update settings (body: `{key: value, ...}`) |
| `GET` | `/api/settings/export` | Export full config as JSON |
| `POST` | `/api/settings/import` | Import config from JSON |

---

## Auth (`/api/auth`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/github/manifest-callback` | GitHub App manifest registration callback |

---

## Wizard (`/api/wizard`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/wizard/status` | Whether first-run wizard has been completed |
| `POST` | `/api/wizard/complete` | Mark wizard complete |

---

## SSE events format

The pipeline log stream (`GET /api/pipelines/{id}/logs`) emits newline-delimited JSON:

```
data: {"level":"info","message":"Starting pipeline hil-stress","pipeline_id":"hil-stress","timestamp":"2026-07-01T12:00:00Z"}
data: {"level":"info","message":"Picked issue #42 in HW-Check","issue":42,"stage":"HW-Check"}
data: {"level":"success","message":"Session complete — stage advanced","issue":42,"stage":"HW-Update"}
data: {"level":"error","message":"Session timed out after 4h","issue":42}
data: {"level":"warn","message":"Model switch: claude-sonnet-4.6 → claude-haiku-4.5"}
```

Fields: `level` (info/warn/error/success), `message`, `pipeline_id`, `issue`, `stage`, `timestamp`
