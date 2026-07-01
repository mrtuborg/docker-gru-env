# Pipeline Engine

The pipeline engine is the core of Gru Server. It polls a GitHub Projects v2 board, picks the most advanced actionable issue, and runs a `gh copilot session` subprocess to process it.

## Concepts

**Pipeline** — a named configuration that ties together:
- a GitHub connector (auth + board location)
- a list of stages (board columns)
- per-stage agent assignments and prompts
- model priority list and timeouts
- optional orchestrator agent

**Stage** — one column on the GitHub Project board. Can be:
- `ai` — processed by the engine (agent + prompt)
- `human` — skipped; waits for human action
- `done` — terminal; not processed

**Pull principle** — the engine always picks the _rightmost_ AI-actionable issue. If an issue is in HW-Log, it's processed before one in HW-Check, maximising pipeline throughput.

## State machine (per issue)

```
                 ┌────────────────────────────────────────┐
                 ▼                                        │ retry (< max_retries)
           [pick issue]                                   │
                 │                                        │
          ┌──────▼──────┐     exit 0 +      ┌────────────┴──────┐
          │ run session │──── stage change──►│ move to next stage│
          └──────┬──────┘                   └───────────────────┘
                 │
          ┌──────▼──────┐
          │  check exit │
          └──────┬──────┘
                 │
        ┌────────┴────────┐
        │                 │
   exit 0 (done)    exit 124 (timeout)
   mark complete    add needs-human label
        │                 │
   ─────┘           attempt++
                     if attempt >= max_retries:
                       mark needs-human permanently
```

## Stage prompt rendering

Before launching a session, the engine substitutes `${VAR}` template variables in the stage prompt:

| Variable | Value |
|----------|-------|
| `${ISSUE_NUM}` | GitHub issue number |
| `${ISSUE_REPO}` | Issue repository (`owner/repo`) |
| `${ISSUE_STAGE}` | Current stage/column name |
| `${REPO}` | `project_owner/project_number` (for legacy compat) |
| `${GH_HOST}` | GitHub hostname from connector |
| `${PROJECT_NUM}` | Projects v2 number |
| `${PROJECT_OWNER}` | Projects v2 owner (org or user) |
| `${ALLOWED_REPOS}` | Space-separated list of allowed repos |

Additional variables come from the stage `env_json` field (merged last, so they can override defaults).

## Model fallback

The `models_json` field contains a priority-ordered list:

```json
[
  {"model": "claude-sonnet-4.6", "priority": 1},
  {"model": "claude-haiku-4.5",  "priority": 2}
]
```

Rules:
- Start with priority 1
- 3 consecutive failures on the current model → switch to next
- Reaching the end of the list → pipeline pauses with error

## Session execution

```python
cmd = [
    "timeout", str(timeout_secs),
    "gh", "copilot", "--",
    "--model", model,
    "--agent", agent_name,  # optional
    "-p", prompt_text,
    "--yolo", "--no-ask-user",
]
# GH_TOKEN injected from connector vault; GH_HOST set so gh knows which server
env = {**os.environ, "GH_HOST": gh_host, "GH_TOKEN": token}
proc = await asyncio.create_subprocess_exec(*cmd, env=env, cwd=working_dir)
```

The session runs as a subprocess with combined stdout/stderr captured. Exit code 0 = success.

## Orchestrator agent

> ⚠️ **Not yet wired in the engine.** The `orchestrator_agent_id` field exists in the schema and the Pipeline Editor UI, but the engine does not currently launch the orchestrator. This is planned for a future iteration.

When implemented, an optional agent with `is_orchestrator = true` assigned to a pipeline (not a stage) will:
- Monitor engine logs for errors
- Classify failures (script errors, auth failures, infrastructure issues)
- Self-heal where possible (retry, adjust prompts)
- Escalate to `needs-human` when blocked

See [agents.md](agents.md) for the orchestrator agent file format.

## Run records

Every pipeline run is logged:

```sql
pipeline_runs:       one row per start/stop (id, pipeline_id, started_at, status, counts)
pipeline_run_items:  one row per issue processed (issue_number, stage, duration_s, model, cost_usd)
pipeline_state:      current per-issue state (attempt_count, status: pending/completed/needs-human)
```

## SSE log stream

Subscribe to live events:

```
GET /api/pipelines/{id}/logs
Content-Type: text/event-stream

data: {"level":"info","message":"Picked issue #42 in HW-Check","issue":42,"stage":"HW-Check"}
data: {"level":"success","message":"Session completed, stage advanced to HW-Update","issue":42}
```

Event levels: `info`, `warn`, `error`, `success`

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pipelines` | List all pipelines |
| `GET` | `/api/pipelines/{id}` | Get pipeline with stages |
| `POST` | `/api/pipelines` | Create pipeline |
| `PUT` | `/api/pipelines/{id}` | Update pipeline |
| `DELETE` | `/api/pipelines/{id}` | Delete pipeline |
| `POST` | `/api/pipelines/{id}/start` | Start the engine |
| `POST` | `/api/pipelines/{id}/stop` | Stop the engine |
| `GET` | `/api/pipelines/{id}/status` | Running/stopped + current issue |
| `GET` | `/api/pipelines/{id}/logs` | SSE log stream |
| `GET` | `/api/pipelines/{id}/runs` | Run history |

## Pipeline YAML format

Pipelines can be imported/exported as YAML via the Pipeline Editor UI:

```yaml
id: hil-stress
name: HIL Stress Testing
plugin_id: ghe-roommate
project_owner: roommate
project_number: 14
poll_interval: 300
max_issues: 10
session_timeout_hours: 4
models:
  - model: claude-sonnet-4.6
    priority: 1
  - model: claude-haiku-4.5
    priority: 2
orchestrator_agent_id: hil-orchestrator
stages:
  - column_name: Todo
    actor: human
  - column_name: HW-Check
    actor: ai
    agent_id: the-vet
    prompt: |
      You are running HW-Check for issue #${ISSUE_NUM}.
      ...
  - column_name: HW-Update
    actor: ai
    agent_id: the-surgeon
    prompt: |
      ...
```

## Database schema

```sql
CREATE TABLE pipelines (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    enabled               INTEGER DEFAULT 1,
    plugin_id             TEXT NOT NULL,        -- connector ID
    project_owner         TEXT,                 -- GitHub org/user
    project_number        INTEGER,              -- GitHub Projects board number
    poll_interval         INTEGER DEFAULT 300,
    max_issues            INTEGER DEFAULT 50,
    max_retries           INTEGER DEFAULT 3,
    session_timeout_hours REAL DEFAULT 4.0,
    models_json           TEXT DEFAULT '[]',
    working_dir           TEXT,
    orchestrator_agent_id TEXT DEFAULT ''
);

CREATE TABLE pipeline_stages (
    pipeline_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    column_name TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'ai',   -- 'ai' | 'human'
    agent_id    TEXT DEFAULT '',
    prompt      TEXT DEFAULT '',
    env_json    TEXT DEFAULT '{}'
);
```
