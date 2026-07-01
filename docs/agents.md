# Agent Library

Agents are the AI actors that process pipeline stages. Each agent is described by a `.agent.md` file with YAML frontmatter.

## Agent file format

```markdown
---
name: The Vet
model: claude-sonnet-4.6
description: Pre-flight health check agent — verifies devices are reachable and on the right firmware before upgrade
tools:
  - bash
  - gh
skills:
  - hil-stress
is_orchestrator: false
---

# The Vet

You are a hardware-in-the-loop test engineer running the HW-Check stage.

Your job:
1. Run `bash skills/hil-stress/hil-preflight.sh "${ISSUE_NUM}"` to fetch issue context
2. SSH to each device in the issue's device table and verify:
   - Device is reachable
   - Current firmware version matches what's expected
3. If all devices pass: move the issue to HW-Update
4. If any device fails: add the `needs-human` label and stop

Do not proceed if pre-flight fails.
```

## Frontmatter fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Display name shown in UI |
| `model` | string | no | Preferred model (overrides pipeline default) |
| `description` | string | no | One-line description |
| `tools` | list | no | Tool names (`bash`, `gh`, `python3`, …) |
| `skills` | list | no | Skill folder names to reference |
| `is_orchestrator` | bool | no | If true, agent can be assigned to pipeline (not stage) |

## Agent naming convention (dark IT humor)

| Agent | Role |
|-------|------|
| **The Janitor** | Cleans up stale state, resets failed runs |
| **The Vet** | HW-Check — diagnoses device health |
| **The Surgeon** | HW-Update — performs the upgrade operation |
| **The Hammerer** | HW-Stress — runs the 30-test HIL suite |
| **The Snitch** | HW-Log — analyses results and files findings |

## Orchestrator agents

An orchestrator has `is_orchestrator: true` and is assigned to the **pipeline** (not a stage).

```markdown
---
name: The Director
is_orchestrator: true
description: Pipeline ops monitor — watches the engine, classifies errors, self-heals where possible
---

# The Director

You are the pipeline orchestration monitor. You run in parallel with all stage agents.

## Phase 1 — Read and plan
Read the current pipeline state and identify any blocked issues.

## Phase 2 — Monitor
Watch the engine log for errors. Classify each failure:
- Script error → check skill script syntax, suggest fix
- Auth failure → check GH_TOKEN, alert human
- Network timeout → retry with exponential backoff
- Infra issue → escalate to needs-human

## Phase 3 — Verdict
After all stages complete, summarise the run:
- Issues processed / succeeded / failed
- Models used and approximate cost
- Recurring failures to track

## Ops monitoring
You can spawn sub-agents via `gh copilot session` for long-running checks.
Pass state via `/tmp/run-state-${ISSUE_NUM}.json`.
```

## Uploading agents

From the **Agents** page:
1. Click **From File** — opens system file picker
2. Select a `.agent.md` file
3. The agent is parsed and saved to the database
4. If `is_orchestrator: true` in frontmatter, the orchestrator toggle is automatically set

## Assigning agents

**To a stage**: open Pipeline Editor → Edit mode → click a stage card → select agent from dropdown.

**To a pipeline (orchestrator)**: open Pipeline Editor → Edit mode → Orchestrator section at top → select from orchestrator-only dropdown.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agents` | List all agents |
| `GET` | `/api/agents/{id}` | Get agent |
| `POST` | `/api/agents` | Create agent |
| `PUT` | `/api/agents/{id}` | Update agent |
| `DELETE` | `/api/agents/{id}` | Delete agent |
| `POST` | `/api/agents/import/file` | Upload `.agent.md` file |
| `GET` | `/api/agents/{id}/export` | Download `.agent.md` file |

## Database schema

```sql
CREATE TABLE agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    model           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    tools_json      TEXT DEFAULT '[]',
    skills_json     TEXT DEFAULT '[]',
    prompt          TEXT DEFAULT '',
    is_orchestrator INTEGER NOT NULL DEFAULT 0,
    lint_errors     TEXT DEFAULT '[]',
    created_at      TEXT,
    updated_at      TEXT
);
```
