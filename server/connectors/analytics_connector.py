"""
Analytics Connector — PostgreSQL storage for pipeline runs, session logs, and cost data.

Two public artefacts:

  IAnalyticsStore   — @runtime_checkable Protocol, the interface used by both the
                      pipeline engine (write) and the Boards / Sessions routers (read).

  AnalyticsConnector — GruConnector subclass that owns an asyncpg pool, implements
                       IAnalyticsStore, and is registered in the connector system so
                       it appears on the Connectors page.

Every pipeline stores analytics_connector_id.  The engine and routers look up that
connector via connector_manager, cast to IAnalyticsStore, and call through the interface.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Protocol, runtime_checkable

from ..connector_base import GruConnector, ConnectorHealth, HealthStatus

logger = logging.getLogger(__name__)


# ── Pricing table (USD per million tokens) ───────────────────────────────────

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-5":        {"input": 3.0,    "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-sonnet-4.6":      {"input": 3.0,    "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-sonnet-4.5":      {"input": 3.0,    "output": 15.0,  "cache_read": 0.30,  "cache_write": 3.75},
    "claude-haiku-4.5":       {"input": 0.80,   "output": 4.0,   "cache_read": 0.08,  "cache_write": 1.0},
    "claude-opus-4.8":        {"input": 15.0,   "output": 75.0,  "cache_read": 1.5,   "cache_write": 18.75},
    "claude-opus-4.7":        {"input": 15.0,   "output": 75.0,  "cache_read": 1.5,   "cache_write": 18.75},
    "gpt-4o":                 {"input": 2.5,    "output": 10.0,  "cache_read": 1.25,  "cache_write": 0.0},
    "gpt-4o-mini":            {"input": 0.15,   "output": 0.60,  "cache_read": 0.075, "cache_write": 0.0},
    "gpt-5.5":                {"input": 2.5,    "output": 10.0,  "cache_read": 1.25,  "cache_write": 0.0},
    "gpt-5.4":                {"input": 2.5,    "output": 10.0,  "cache_read": 1.25,  "cache_write": 0.0},
    "gpt-5-mini":             {"input": 0.15,   "output": 0.60,  "cache_read": 0.075, "cache_write": 0.0},
    "o3":                     {"input": 10.0,   "output": 40.0,  "cache_read": 2.5,   "cache_write": 0.0},
    "o4-mini":                {"input": 1.10,   "output": 4.40,  "cache_read": 0.275, "cache_write": 0.0},
    "gemini-2.5-pro":         {"input": 1.25,   "output": 10.0,  "cache_read": 0.31,  "cache_write": 4.5},
    "gemini-2.5-flash":       {"input": 0.1875, "output": 0.70,  "cache_read": 0.018, "cache_write": 1.0},
    "gemini-3.1-pro-preview": {"input": 1.25,   "output": 10.0,  "cache_read": 0.31,  "cache_write": 4.5},
    "gemini-3.5-flash":       {"input": 0.1875, "output": 0.70,  "cache_read": 0.018, "cache_write": 1.0},
}


def _compute_cost_usd(model: str, shutdown_data: dict) -> float | None:
    """Compute cost from shutdown token data and model pricing.

    Uses fine-grained tokenDetails (distinguishes fresh vs cached input) so each
    token type is billed at its correct rate.
    """
    price = MODEL_PRICING.get(model)
    if not price:
        for key in MODEL_PRICING:           # prefix match (e.g. "claude-sonnet-4.6-20250514")
            if model.startswith(key):
                price = MODEL_PRICING[key]
                break
    if not price:
        return None

    td = shutdown_data.get("tokenDetails", {})
    fresh_input   = td.get("input",      {}).get("tokenCount", 0)
    cache_read    = td.get("cache_read", {}).get("tokenCount", 0)
    cache_write   = td.get("cache_write",{}).get("tokenCount", 0)
    output_tokens = td.get("output",     {}).get("tokenCount", 0)

    # Fall back to modelMetrics if tokenDetails are missing
    if not any([fresh_input, cache_read, cache_write, output_tokens]):
        for m_data in shutdown_data.get("modelMetrics", {}).values():
            u = m_data.get("usage", {})
            fresh_input   = u.get("inputTokens", 0)
            cache_read    = u.get("cacheReadTokens", 0)
            cache_write   = u.get("cacheWriteTokens", 0)
            output_tokens = u.get("outputTokens", 0)
            break

    M = 1_000_000
    return round(
        fresh_input   * price["input"]       / M
        + cache_read  * price["cache_read"]  / M
        + cache_write * price["cache_write"] / M
        + output_tokens * price["output"]    / M,
        8,
    )


# ── Interface ─────────────────────────────────────────────────────────────────

@runtime_checkable
class IAnalyticsStore(Protocol):
    """
    Pipeline analytics interface.

    Write path (pipeline engine):
        create_run → write_run_item (×N) / write_session_logs (×N) → finish_run

    Read path (Boards / Sessions & Cost routers):
        read_sessions, read_issue_history, read_session_logs
    All reads are scoped to pipeline_id so the connector never leaks data across
    pipelines sharing the same Postgres instance.
    """

    # ── Write ─────────────────────────────────────────────────────────────────

    async def create_run(self, pipeline_id: str, run_id: str) -> None:
        """Record the start of a pipeline engine pass."""
        ...

    async def finish_run(self, run_id: str, status: str, counts: dict) -> None:
        """Mark a pass as completed or failed with aggregate counters."""
        ...

    async def write_run_item(
        self, run_id: str, item: dict, shutdown_data: dict | None = None
    ) -> None:
        """Persist a single (issue × stage) session result.

        item keys: issue_number, issue_repo, issue_title, stage, status,
                   started_at, ended_at, duration_s, model, session_id, error_message
        shutdown_data: full session.shutdown event data from events.jsonl
        """
        ...

    async def write_session_logs(
        self, run_id: str, issue_number: int, stage: str, lines: list[str]
    ) -> None:
        """Batch-insert raw Copilot CLI output lines."""
        ...

    # ── Read ──────────────────────────────────────────────────────────────────

    async def read_sessions(self, pipeline_id: str, days: int = 7) -> dict:
        """Return analytics summary + flat session list for a pipeline."""
        ...

    async def read_issue_history(
        self, pipeline_id: str, issue_number: int
    ) -> list[dict]:
        """All run items for an issue in this pipeline, newest first."""
        ...

    async def read_session_logs(
        self, run_id: str, issue_number: int, stage: str | None = None
    ) -> list[dict]:
        """Return stored log lines for a run item, oldest first."""
        ...


# ── Connector ─────────────────────────────────────────────────────────────────

_DDL = """
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
"""


class AnalyticsConnector(GruConnector):
    """
    PostgreSQL analytics connector — implements IAnalyticsStore.

    One instance per pipeline group (multiple pipelines may share one connector).
    Config fields:  host, port, database, user
    Vault key:      "password"
    Env fallback:   ANALYTICS_DB_URL (for docker-compose / server-run.sh setups)
    """

    _pool: object | None  # asyncpg.Pool when connected

    def __init__(self, plugin_id: str, config: dict) -> None:
        super().__init__(plugin_id, config)
        self._pool = None

    # ── GruConnector identity ─────────────────────────────────────────────────

    @property
    def connector_type(self) -> str:
        return "analytics"

    @property
    def display_name(self) -> str:
        db = self._config.get("database", "gru_analytics")
        host = self._config.get("host", "")
        return f"Analytics DB ({db}@{host})" if host else f"Analytics DB ({db})"

    @property
    def description(self) -> str:
        return "PostgreSQL store for pipeline run history, session logs, and cost data"

    @property
    def icon(self) -> str:
        return "Database"

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["host"],
            "properties": {
                "host":     {"type": "string",  "title": "Host / IP", "placeholder": "192.168.1.100 or hostname"},
                "port":     {"type": "integer", "title": "Port",      "default": 5432},
                "database": {"type": "string",  "title": "Database",  "default": "gru_analytics"},
                "user":     {"type": "string",  "title": "User",      "default": "gru",
                             "description": "Must exist in Postgres. No password required (trust auth)."},
            },
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def configure(self, config: dict) -> None:
        self._config = config
        # Close existing pool so _connect() rebuilds with new connection params
        await self.teardown()
        await self._connect()

    async def _connect(self) -> None:
        """Build the asyncpg pool and run DDL migrations."""
        if self._pool is not None:
            return

        url = self._build_url()
        if not url:
            logger.warning("Analytics connector %s: no URL — skipping connect", self.plugin_id)
            return
        try:
            import asyncpg  # type: ignore[import]
            self._pool = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=5,
                command_timeout=30,
                server_settings={"application_name": f"gru-{self.plugin_id}"},
            )
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(_DDL)
            logger.info("Analytics connector %s connected to %s", self.plugin_id, url)
        except Exception as exc:
            logger.warning("Analytics connector %s: connect failed: %s", self.plugin_id, exc)
            self._pool = None

    def _build_url(self) -> str:
        """Assemble the Postgres DSN from config (no password — trust auth)."""
        env_url = os.environ.get("ANALYTICS_DB_URL", "")
        if env_url and not self._config.get("host"):
            return env_url                   # env var wins when no explicit config

        host = self._config.get("host", "")
        if not host:
            return ""                        # misconfigured — caller will skip connect

        # Translate loopback addresses to host.docker.internal so that
        # "localhost" entered in the UI reaches the host machine, not the container.
        if host in ("localhost", "127.0.0.1", "::1"):
            host = "host.docker.internal"

        port = int(self._config.get("port", 5432))
        db   = self._config.get("database", "gru_analytics")
        user = self._config.get("user", "gru")

        return f"postgresql://{user}@{host}:{port}/{db}"

    async def health(self) -> ConnectorHealth:
        if self._pool is None:
            return ConnectorHealth(HealthStatus.ERROR, "Not connected")
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                result = await conn.fetchval(
                    "SELECT COUNT(*) FROM pipeline_runs"
                )
            return ConnectorHealth(
                HealthStatus.HEALTHY,
                f"{result} runs stored",
            )
        except Exception as exc:
            return ConnectorHealth(HealthStatus.ERROR, str(exc))

    async def teardown(self) -> None:
        if self._pool is not None:
            await self._pool.close()  # type: ignore[union-attr]
            self._pool = None

    # ── IAnalyticsStore — Write ───────────────────────────────────────────────

    async def create_run(self, pipeline_id: str, run_id: str) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    """INSERT INTO pipeline_runs(id, pipeline_id)
                       VALUES($1, $2) ON CONFLICT(id) DO NOTHING""",
                    run_id, pipeline_id,
                )
        except Exception as exc:
            logger.warning("analytics.create_run: %s", exc)

    async def finish_run(self, run_id: str, status: str, counts: dict) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    """UPDATE pipeline_runs
                       SET ended_at=now(), status=$1,
                           issues_processed=$2, issues_succeeded=$3,
                           issues_failed=$4, issues_skipped=$5, model_used=$6
                       WHERE id=$7""",
                    status,
                    counts.get("processed", 0), counts.get("succeeded", 0),
                    counts.get("failed", 0),    counts.get("skipped", 0),
                    counts.get("model"),
                    run_id,
                )
        except Exception as exc:
            logger.warning("analytics.finish_run: %s", exc)

    async def write_run_item(
        self, run_id: str, item: dict, shutdown_data: dict | None = None
    ) -> None:
        if not self._pool:
            return

        sd = shutdown_data or {}
        td = sd.get("tokenDetails", {})
        mm = sd.get("modelMetrics", {})
        model_key = item.get("model") or sd.get("currentModel")

        fresh_input   = td.get("input",      {}).get("tokenCount")
        output_tokens = td.get("output",     {}).get("tokenCount")
        cache_read    = td.get("cache_read", {}).get("tokenCount")
        cache_write   = td.get("cache_write",{}).get("tokenCount")
        total_input   = None
        reasoning     = None
        api_requests  = None

        if model_key and model_key in mm:
            m_usage = mm[model_key].get("usage", {})
            total_input  = m_usage.get("inputTokens")
            reasoning    = m_usage.get("reasoningTokens")
            api_requests = mm[model_key].get("requests", {}).get("count")
            if fresh_input is None:          # fallback: no tokenDetails
                fresh_input   = m_usage.get("inputTokens")
                output_tokens = m_usage.get("outputTokens")
                cache_read    = m_usage.get("cacheReadTokens")
                cache_write   = m_usage.get("cacheWriteTokens")

        code = sd.get("codeChanges", {})
        cost = item.get("cost_usd")
        if cost is None and model_key and sd:
            cost = _compute_cost_usd(model_key, sd)

        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    """INSERT INTO pipeline_run_items(
                           run_id, issue_number, issue_repo, issue_title, stage,
                           status, started_at, ended_at, duration_s,
                           model, cost_usd, session_id, error_message,
                           tokens_input, tokens_output, tokens_cache_read,
                           tokens_cache_write, tokens_reasoning, total_input_tokens,
                           nano_aiu, premium_requests, api_requests, api_duration_ms,
                           shutdown_type, context_tokens, system_tokens,
                           conversation_tokens, lines_added, lines_removed,
                           files_modified, shutdown_data
                       ) VALUES(
                           $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                           $14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,
                           $25,$26,$27,$28,$29,$30,$31
                       )
                       ON CONFLICT(run_id, issue_number, stage) DO UPDATE SET
                           status=$6, ended_at=$8, duration_s=$9, cost_usd=$11,
                           session_id=$12, error_message=$13,
                           tokens_input=$14, tokens_output=$15,
                           tokens_cache_read=$16, tokens_cache_write=$17,
                           tokens_reasoning=$18, total_input_tokens=$19,
                           nano_aiu=$20, premium_requests=$21, api_requests=$22,
                           api_duration_ms=$23, shutdown_type=$24,
                           context_tokens=$25, system_tokens=$26,
                           conversation_tokens=$27, lines_added=$28,
                           lines_removed=$29, files_modified=$30, shutdown_data=$31""",
                    run_id,
                    item["issue_number"], item["issue_repo"], item.get("issue_title"),
                    item["stage"], item["status"],
                    item.get("started_at"), item.get("ended_at"), item.get("duration_s"),
                    model_key, cost, item.get("session_id"), item.get("error_message"),
                    fresh_input, output_tokens, cache_read, cache_write,
                    reasoning, total_input,
                    sd.get("totalNanoAiu"), sd.get("totalPremiumRequests"),
                    api_requests, sd.get("totalApiDurationMs"),
                    sd.get("shutdownType"),
                    sd.get("currentTokens"), sd.get("systemTokens"),
                    sd.get("conversationTokens"),
                    code.get("linesAdded", 0), code.get("linesRemoved", 0),
                    json.dumps(code.get("filesModified", [])),
                    json.dumps(sd) if sd else None,
                )
        except Exception as exc:
            logger.warning("analytics.write_run_item: %s", exc)

    async def write_session_logs(
        self, run_id: str, issue_number: int, stage: str, lines: list[str]
    ) -> None:
        if not self._pool or not lines:
            return
        rows = [(run_id, issue_number, stage, "session_log", line) for line in lines]
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.executemany(
                    """INSERT INTO session_logs(run_id, issue_number, stage, level, message)
                       VALUES($1,$2,$3,$4,$5)""",
                    rows,
                )
        except Exception as exc:
            logger.warning("analytics.write_session_logs: %s", exc)

    # ── IAnalyticsStore — Read ────────────────────────────────────────────────

    async def read_sessions(self, pipeline_id: str, days: int = 7) -> dict:
        """Aggregate analytics + flat session list, scoped to pipeline_id."""
        if not self._pool:
            return {"summary": {}, "sessions": [], "analytics_unavailable": True}

        days = max(0, days)
        time_clause = "AND ri.started_at >= now() - ($2 || ' days')::INTERVAL" if days > 0 else ""

        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                params = (pipeline_id, str(days)) if days > 0 else (pipeline_id,)

                agg = await conn.fetchrow(
                    f"""SELECT COUNT(*) AS total,
                               SUM(CASE WHEN ri.status IN ('success','completed','done')
                                   THEN 1 ELSE 0 END) AS succeeded,
                               SUM(ri.cost_usd)   AS total_cost_usd,
                               AVG(ri.cost_usd)   AS avg_cost_usd,
                               AVG(ri.duration_s) AS avg_duration_s,
                               SUM(ri.tokens_input)      AS total_tokens_input,
                               SUM(ri.tokens_output)     AS total_tokens_output,
                               SUM(ri.tokens_cache_read) AS total_cache_read,
                               SUM(ri.tokens_reasoning)  AS total_reasoning,
                               SUM(ri.nano_aiu)          AS total_nano_aiu,
                               SUM(ri.premium_requests)  AS total_premium_requests,
                               SUM(ri.api_requests)      AS total_api_requests,
                               SUM(ri.lines_added)       AS total_lines_added,
                               SUM(ri.lines_removed)     AS total_lines_removed
                        FROM pipeline_run_items ri
                        JOIN pipeline_runs pr ON ri.run_id = pr.id
                        WHERE pr.pipeline_id = $1 {time_clause}""",
                    *params,
                ) or {}

                by_stage = {
                    r["stage"]: dict(r)
                    for r in await conn.fetch(
                        f"""SELECT ri.stage,
                                   COUNT(*) AS count,
                                   SUM(CASE WHEN ri.status IN ('success','completed','done')
                                       THEN 1 ELSE 0 END) AS succeeded,
                                   SUM(ri.cost_usd)   AS cost_usd,
                                   AVG(ri.duration_s) AS avg_duration_s,
                                   SUM(ri.tokens_input)  AS tokens_input,
                                   SUM(ri.tokens_output) AS tokens_output,
                                   SUM(ri.nano_aiu)      AS nano_aiu
                            FROM pipeline_run_items ri
                            JOIN pipeline_runs pr ON ri.run_id = pr.id
                            WHERE pr.pipeline_id = $1 {time_clause}
                            GROUP BY ri.stage ORDER BY count DESC""",
                        *params,
                    )
                }

                by_model = {
                    r["model"]: dict(r)
                    for r in await conn.fetch(
                        f"""SELECT COALESCE(ri.model,'unknown') AS model,
                                   COUNT(*) AS count,
                                   SUM(ri.cost_usd)   AS cost_usd,
                                   AVG(ri.duration_s) AS avg_duration_s,
                                   SUM(ri.tokens_input)  AS tokens_input,
                                   SUM(ri.tokens_output) AS tokens_output,
                                   SUM(ri.api_requests)  AS api_requests
                            FROM pipeline_run_items ri
                            JOIN pipeline_runs pr ON ri.run_id = pr.id
                            WHERE pr.pipeline_id = $1 {time_clause}
                            GROUP BY ri.model ORDER BY count DESC""",
                        *params,
                    )
                }

                sessions = [
                    dict(r)
                    for r in await conn.fetch(
                        f"""SELECT ri.run_id, ri.issue_number, ri.issue_repo, ri.issue_title,
                                   ri.stage, ri.status,
                                   ri.started_at::text, ri.ended_at::text,
                                   ri.duration_s, ri.model, ri.cost_usd, ri.session_id,
                                   ri.tokens_input, ri.tokens_output, ri.tokens_cache_read,
                                   ri.tokens_reasoning, ri.nano_aiu, ri.premium_requests,
                                   ri.api_requests, ri.shutdown_type,
                                   ri.lines_added, ri.lines_removed
                            FROM pipeline_run_items ri
                            JOIN pipeline_runs pr ON ri.run_id = pr.id
                            WHERE pr.pipeline_id = $1 {time_clause}
                            ORDER BY ri.started_at DESC LIMIT 1000""",
                        *params,
                    )
                ]

            total     = agg.get("total") or 0
            succeeded = agg.get("succeeded") or 0
            return {
                "summary": {
                    "total":     total,
                    "succeeded": succeeded,
                    "failed":    total - succeeded,
                    "success_rate": round(succeeded / total * 100, 1) if total else 0.0,
                    "total_cost_usd":         round(float(agg.get("total_cost_usd")  or 0), 6),
                    "avg_cost_usd":           round(float(agg.get("avg_cost_usd")    or 0), 6),
                    "avg_duration_s":         round(float(agg.get("avg_duration_s")  or 0), 1),
                    "total_tokens_input":     int(agg.get("total_tokens_input")    or 0),
                    "total_tokens_output":    int(agg.get("total_tokens_output")   or 0),
                    "total_cache_read":       int(agg.get("total_cache_read")      or 0),
                    "total_reasoning":        int(agg.get("total_reasoning")       or 0),
                    "total_nano_aiu":         int(agg.get("total_nano_aiu")        or 0),
                    "total_premium_requests": int(agg.get("total_premium_requests") or 0),
                    "total_api_requests":     int(agg.get("total_api_requests")    or 0),
                    "total_lines_added":      int(agg.get("total_lines_added")     or 0),
                    "total_lines_removed":    int(agg.get("total_lines_removed")   or 0),
                    "by_stage": {
                        k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv)
                            for kk, vv in v.items()}
                        for k, v in by_stage.items()
                    },
                    "by_model": {
                        k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv)
                            for kk, vv in v.items()}
                        for k, v in by_model.items()
                    },
                },
                "sessions": sessions,
            }
        except Exception as exc:
            logger.warning("analytics.read_sessions: %s", exc)
            return {"summary": {}, "sessions": [], "analytics_unavailable": True}

    async def read_issue_history(
        self, pipeline_id: str, issue_number: int
    ) -> list[dict]:
        """All run items for an issue in this pipeline, newest first."""
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                rows = await conn.fetch(
                    """SELECT ri.run_id, ri.stage, ri.status,
                              ri.started_at::text, ri.ended_at::text,
                              ri.duration_s, ri.model, ri.cost_usd, ri.session_id,
                              ri.error_message,
                              ri.tokens_input, ri.tokens_output, ri.tokens_cache_read,
                              ri.tokens_reasoning, ri.total_input_tokens,
                              ri.nano_aiu, ri.premium_requests, ri.api_requests,
                              ri.api_duration_ms, ri.shutdown_type,
                              ri.context_tokens, ri.lines_added, ri.lines_removed,
                              ri.files_modified
                       FROM pipeline_run_items ri
                       JOIN pipeline_runs pr ON ri.run_id = pr.id
                       WHERE pr.pipeline_id = $1 AND ri.issue_number = $2
                       ORDER BY ri.started_at DESC
                       LIMIT 100""",
                    pipeline_id, issue_number,
                )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("analytics.read_issue_history: %s", exc)
            return []

    async def read_session_logs(
        self, run_id: str, issue_number: int, stage: str | None = None
    ) -> list[dict]:
        """Return stored log lines for a run item, oldest first."""
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                if stage:
                    rows = await conn.fetch(
                        """SELECT level, message, ts::text, stage
                           FROM session_logs
                           WHERE run_id=$1 AND issue_number=$2 AND stage=$3
                           ORDER BY id ASC""",
                        run_id, issue_number, stage,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT level, message, ts::text, stage
                           FROM session_logs
                           WHERE run_id=$1 AND issue_number=$2
                           ORDER BY id ASC""",
                        run_id, issue_number,
                    )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("analytics.read_session_logs: %s", exc)
            return []
