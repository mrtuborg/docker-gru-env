-- gru-analytics-db schema — auto-applied by postgres on first init
-- (via /docker-entrypoint-initdb.d/). Mirrors server/connectors/analytics_connector.py's
-- _DDL so the schema exists even if gru-server has never connected yet.
-- Kept in sync manually — CREATE ... IF NOT EXISTS makes both sources idempotent.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                TEXT        PRIMARY KEY,
    pipeline_id       TEXT        NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at          TIMESTAMPTZ,
    status            TEXT        DEFAULT 'running',
    issues_processed  INTEGER     DEFAULT 0,
    issues_succeeded  INTEGER     DEFAULT 0,
    issues_failed     INTEGER     DEFAULT 0,
    issues_skipped    INTEGER     DEFAULT 0,
    model_used        TEXT
);
CREATE INDEX IF NOT EXISTS pipeline_runs_pid_ts
    ON pipeline_runs (pipeline_id, started_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_run_items (
    run_id              TEXT        NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    issue_number        INTEGER     NOT NULL,
    issue_repo          TEXT        NOT NULL,
    issue_title         TEXT,
    stage               TEXT        NOT NULL,
    status              TEXT        NOT NULL,
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    duration_s          REAL,
    model               TEXT,
    cost_usd            REAL,
    session_id          TEXT,
    error_message       TEXT,

    -- Fine-grained token counts (from session.shutdown tokenDetails)
    tokens_input        INTEGER,    -- fresh (non-cached) input tokens
    tokens_output       INTEGER,    -- output / completion tokens
    tokens_cache_read   INTEGER,    -- tokens served from prompt cache
    tokens_cache_write  INTEGER,    -- tokens written to prompt cache
    tokens_reasoning    INTEGER,    -- reasoning tokens (o-series / extended thinking)
    total_input_tokens  INTEGER,    -- all input including cache reads (modelMetrics.usage)

    -- Cost attribution
    nano_aiu            BIGINT,     -- GitHub Copilot nano AI Units (totalNanoAiu)
    premium_requests    INTEGER,    -- totalPremiumRequests
    api_requests        INTEGER,    -- modelMetrics[model].requests.count
    api_duration_ms     INTEGER,    -- totalApiDurationMs

    -- Session health
    shutdown_type       TEXT,       -- "routine" | "timeout" | "error"
    context_tokens      INTEGER,    -- currentTokens (active context window at shutdown)
    system_tokens       INTEGER,    -- systemTokens
    conversation_tokens INTEGER,    -- conversationTokens

    -- Code changes made by the agent during the session
    lines_added         INTEGER     DEFAULT 0,
    lines_removed       INTEGER     DEFAULT 0,
    files_modified      TEXT,       -- JSON array

    -- Full shutdown payload for ad-hoc analysis
    shutdown_data       JSONB,

    PRIMARY KEY (run_id, issue_number, stage)
);
CREATE INDEX IF NOT EXISTS run_items_issue
    ON pipeline_run_items (issue_number, started_at DESC);
CREATE INDEX IF NOT EXISTS run_items_run
    ON pipeline_run_items (run_id);

CREATE TABLE IF NOT EXISTS session_logs (
    id            BIGSERIAL   PRIMARY KEY,
    run_id        TEXT        NOT NULL,
    issue_number  INTEGER     NOT NULL,
    stage         TEXT        NOT NULL,
    level         TEXT        NOT NULL DEFAULT 'session_log',
    message       TEXT        NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_logs_run
    ON session_logs (run_id, issue_number, stage);
CREATE INDEX IF NOT EXISTS session_logs_ts
    ON session_logs (ts DESC);
