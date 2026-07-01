# Database Schema

Gru Server uses SQLite with WAL mode and foreign key enforcement. The database lives at `/data/gru/server.db` inside the container (mapped from the host via Docker volume).

## Tables

### `settings`

Global key-value configuration.

```sql
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

Common keys: `theme`, `timezone`, `default_model`

---

### `plugins`

Installed connectors.

```sql
CREATE TABLE plugins (
    id          TEXT PRIMARY KEY,    -- e.g. "ghe-roommate"
    plugin_type TEXT NOT NULL,       -- "github" | "copilot" | "azure" | "obsidian"
    config      TEXT NOT NULL DEFAULT '{}',  -- JSON, no secrets
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    updated_at  TEXT
);
```

---

### `credentials`

Encrypted secrets keyed by (plugin_id, key). Values are AES-256-GCM encrypted blobs.

```sql
CREATE TABLE credentials (
    plugin_id  TEXT NOT NULL,
    key        TEXT NOT NULL,   -- e.g. "token"
    value      BLOB NOT NULL,   -- encrypted
    expires_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (plugin_id, key)
);
```

---

### `agents`

AI agent definitions.

```sql
CREATE TABLE agents (
    id              TEXT PRIMARY KEY,   -- e.g. "the-vet"
    name            TEXT NOT NULL,      -- "The Vet"
    model           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    tools_json      TEXT DEFAULT '[]',  -- ["bash", "gh"]
    skills_json     TEXT DEFAULT '[]',  -- ["hil-stress"]
    prompt          TEXT DEFAULT '',    -- full agent prompt body
    is_orchestrator INTEGER NOT NULL DEFAULT 0,
    lint_errors     TEXT DEFAULT '[]',
    created_at      TEXT,
    updated_at      TEXT
);
```

---

### `pipelines`

Pipeline configurations.

```sql
CREATE TABLE pipelines (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    enabled               INTEGER DEFAULT 1,
    plugin_id             TEXT NOT NULL,        -- references plugins.id
    board_type            TEXT DEFAULT 'github',
    project_owner         TEXT,                 -- GitHub org or user
    project_number        INTEGER,              -- Projects v2 number
    board_path            TEXT,
    poll_interval         INTEGER DEFAULT 300,  -- seconds
    max_issues            INTEGER DEFAULT 50,
    max_retries           INTEGER DEFAULT 3,
    session_timeout_hours REAL DEFAULT 4.0,
    models_json           TEXT DEFAULT '[]',    -- [{model, priority}]
    allowed_repos_json    TEXT DEFAULT '[]',
    findings_json         TEXT,                 -- findings board config
    working_dir           TEXT,                 -- /workspace or host path
    orchestrator_agent_id TEXT DEFAULT '',      -- references agents.id
    created_at            TEXT,
    updated_at            TEXT
);
```

---

### `pipeline_stages`

Stage definitions per pipeline (board columns → AI configuration).

```sql
CREATE TABLE pipeline_stages (
    pipeline_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL,
    column_name TEXT NOT NULL,          -- must match GitHub project column
    actor       TEXT NOT NULL DEFAULT 'ai',  -- 'ai' | 'human'
    agent_id    TEXT DEFAULT '',        -- references agents.id
    task_prompt TEXT DEFAULT '',        -- short task description
    prompt      TEXT DEFAULT '',        -- full rendered prompt template
    on_success  TEXT DEFAULT '',
    on_failure  TEXT DEFAULT '',
    on_timeout  TEXT DEFAULT '',
    env_json    TEXT DEFAULT '{}',      -- extra env vars for this stage
    PRIMARY KEY (pipeline_id, column_name),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
);
```

---

### `pipeline_runs`

One row per pipeline start/stop.

```sql
CREATE TABLE pipeline_runs (
    id               TEXT PRIMARY KEY,
    pipeline_id      TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    status           TEXT DEFAULT 'running',  -- running|completed|stopped|error
    issues_processed INTEGER DEFAULT 0,
    issues_succeeded INTEGER DEFAULT 0,
    issues_failed    INTEGER DEFAULT 0,
    issues_skipped   INTEGER DEFAULT 0,
    model_used       TEXT,
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
);
```

---

### `pipeline_run_items`

One row per issue processed in a run.

```sql
CREATE TABLE pipeline_run_items (
    run_id        TEXT NOT NULL,
    issue_number  INTEGER NOT NULL,
    issue_repo    TEXT NOT NULL,
    stage         TEXT NOT NULL,
    status        TEXT NOT NULL,   -- success|failed|skipped|timeout
    started_at    TEXT,
    ended_at      TEXT,
    duration_s    REAL,
    model         TEXT,
    cost_usd      REAL,
    session_id    TEXT,
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id) ON DELETE CASCADE
);
```

---

### `pipeline_state`

Persistent per-issue state: attempt counts, completion tokens.

```sql
CREATE TABLE pipeline_state (
    pipeline_id   TEXT NOT NULL,
    issue_key     TEXT NOT NULL,       -- "owner/repo#number"
    status        TEXT NOT NULL,       -- pending|completed|needs-human
    attempt_count INTEGER DEFAULT 0,
    updated_at    TEXT,
    PRIMARY KEY (pipeline_id, issue_key),
    FOREIGN KEY (pipeline_id) REFERENCES pipelines(id) ON DELETE CASCADE
);
```

---

### `quick_actions`

Quick Action definitions.

```sql
CREATE TABLE quick_actions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    action_type TEXT NOT NULL DEFAULT 'create_issue',
    pipeline_id TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}'
    -- config_json: {stage, repo, labels, skill}
);
```

---

### `env_variables`

Global environment variables (plaintext).

```sql
CREATE TABLE env_variables (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    updated_at  TEXT
);
```

---

### `env_secrets`

Global environment secrets (encrypted).

```sql
CREATE TABLE env_secrets (
    name        TEXT PRIMARY KEY,
    value       BLOB NOT NULL,          -- AES-256-GCM encrypted
    description TEXT NOT NULL DEFAULT '',
    updated_at  TEXT
);
```

---

## Migrations

Migrations run at startup via `config.py:init_db()`. They are additive-only (ALTER TABLE ADD COLUMN) and safe to re-run — failures are silently ignored (column already exists).

```python
migrations = [
    "ALTER TABLE pipelines ADD COLUMN working_dir TEXT",
    "ALTER TABLE agents ADD COLUMN skills_json TEXT DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN is_orchestrator INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE pipelines ADD COLUMN orchestrator_agent_id TEXT DEFAULT ''",
]
```

To add a new column: append to this list. The server picks it up on next restart.

**Never rename or drop columns** — use a new column and migrate data in application code.

## Backup and restore

```bash
# Backup (from host)
docker cp gru-server-dev:/data/gru/server.db ./backup-$(date +%Y%m%d).db
docker cp gru-server-dev:/data/gru/vault.key ./vault.key.backup

# Restore
docker cp ./backup-20260701.db gru-server-dev:/data/gru/server.db
docker cp ./vault.key.backup gru-server-dev:/data/gru/vault.key
docker restart gru-server-dev
```

> The vault key is required to decrypt all secrets. Without it, secrets in `credentials` and `env_secrets` are permanently lost.
