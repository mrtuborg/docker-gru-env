"""
Pipeline Engine — replicates watcher-run.sh logic in Python.

Responsibilities:
  1. Query board via GraphQL (pull principle: rightmost AI stage first)
  2. Pick next actionable issue (resume state, retry cap)
  3. Render stage prompt (envsubst-style template variables)
  4. Execute Copilot session (subprocess with timeout + model flag)
  5. Detect stage progression after session
  6. Model fallback (3 consecutive failures → switch)
  7. State persistence (completed tokens + attempt counts)
  8. Run loop (poll → pick → process → repeat)
  9. Emit structured log events for SSE consumers
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Callable, Optional

import httpx

from ..config import (
    get_pipeline, upsert_pipeline,
    create_pipeline_run, finish_pipeline_run, add_pipeline_run_item,
    get_pipeline_state, set_pipeline_state, clear_pipeline_state,
    get_agent,
)
from ..connectors.analytics_connector import IAnalyticsStore
from ..vault import load_secret

logger = logging.getLogger(__name__)


def _gh_host_for(plugin_id: str) -> str:
    """Read the GitHub host from the loaded connector config, fall back to env/default."""
    try:
        # Import lazily to avoid circular imports at module load time
        from ..app import connector_manager
        if connector_manager:
            connector = connector_manager.get(plugin_id)
            if connector and hasattr(connector, "_config"):
                return connector._config.get("host", "github.com")
    except Exception:
        pass
    return os.environ.get("GH_HOST", "github.com")


def _analytics_store_for(analytics_id: str) -> IAnalyticsStore | None:
    """Look up the analytics connector by ID and return it as IAnalyticsStore, or None."""
    if not analytics_id:
        return None
    try:
        from ..app import connector_manager
        if connector_manager:
            conn = connector_manager.get(analytics_id)
            if isinstance(conn, IAnalyticsStore):
                return conn
    except Exception:
        pass
    return None

# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class BoardIssue:
    number: int
    repo: str
    stage: str
    title: str = ""
    labels: list = None  # type: ignore[assignment]
    updated_at: str = ""

    def __post_init__(self):
        if self.labels is None:
            self.labels = []


@dataclass
class SessionResult:
    exit_code: int
    duration_s: float
    stage_changed: bool = False
    new_stage: str = ""
    timed_out: bool = False
    session_id: str = ""
    shutdown_data: dict = field(default_factory=dict)   # full session.shutdown event data
    output_lines: list = field(default_factory=list)    # raw CLI output for log storage


@dataclass
class LogEvent:
    """Structured log event for SSE streaming."""
    level: str        # info, warn, error, success
    message: str
    pipeline_id: str = ""
    issue: int = 0
    stage: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


# ── Log bus (in-memory pub/sub for SSE) ───────────────────────────────────────

class LogBus:
    """Simple in-memory pub/sub for pipeline log events."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, pipeline_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(pipeline_id, []).append(q)
        return q

    def unsubscribe(self, pipeline_id: str, q: asyncio.Queue):
        subs = self._subscribers.get(pipeline_id, [])
        if q in subs:
            subs.remove(q)

    def emit(self, event: LogEvent):
        pid = event.pipeline_id
        for q in self._subscribers.get(pid, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop oldest if consumer is slow


# Global log bus singleton
log_bus = LogBus()


# ── Pipeline Engine ───────────────────────────────────────────────────────────

class PipelineEngine:
    """Manages running pipeline watchers."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_flags: dict[str, asyncio.Event] = {}
        # Live state exposed to the status API
        self._active: dict[str, dict | None] = {}   # pipeline_id → active issue dict
        self._queue: dict[str, list[dict]] = {}     # pipeline_id → queued issue list

    def is_running(self, pipeline_id: str) -> bool:
        task = self._tasks.get(pipeline_id)
        return task is not None and not task.done()

    def live_state(self, pipeline_id: str) -> dict:
        return {
            "active": self._active.get(pipeline_id),
            "queued": self._queue.get(pipeline_id, []),
        }

    async def start(self, pipeline_id: str) -> bool:
        if self.is_running(pipeline_id):
            return False
        stop = asyncio.Event()
        self._stop_flags[pipeline_id] = stop
        self._tasks[pipeline_id] = asyncio.create_task(
            self._run_loop(pipeline_id, stop)
        )
        return True

    async def stop(self, pipeline_id: str) -> bool:
        stop = self._stop_flags.get(pipeline_id)
        if stop:
            stop.set()
        task = self._tasks.pop(pipeline_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._stop_flags.pop(pipeline_id, None)
        return True

    async def run_once(self, pipeline_id: str) -> dict:
        """Execute a single pass (no loop)."""
        pipeline = await get_pipeline(pipeline_id)
        if not pipeline:
            return {"error": "Pipeline not found"}
        return await self._single_pass(pipeline)

    async def stop_all(self):
        for pid in list(self._tasks.keys()):
            await self.stop(pid)

    def status(self, pipeline_id: str) -> str:
        if self.is_running(pipeline_id):
            return "running"
        return "stopped"

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run_loop(self, pipeline_id: str, stop: asyncio.Event):
        self._emit(pipeline_id, "info", "Pipeline watcher started")
        try:
            while not stop.is_set():
                pipeline = await get_pipeline(pipeline_id)
                if not pipeline or not pipeline.get("enabled"):
                    self._emit(pipeline_id, "info", "Pipeline disabled, stopping")
                    break

                result = await self._single_pass(pipeline)

                if result.get("issues_processed", 0) == 0:
                    poll = pipeline.get("poll_interval", 300)
                    self._emit(pipeline_id, "info", f"No actionable issues, sleeping {poll}s")
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=poll)
                        break  # stop was set
                    except asyncio.TimeoutError:
                        pass  # poll interval elapsed, loop again
                # If we processed issues, immediately loop to check for more
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._emit(pipeline_id, "error", f"Pipeline crashed: {e}")
            logger.exception("Pipeline %s crashed", pipeline_id)
        finally:
            self._emit(pipeline_id, "info", "Pipeline watcher stopped")

    # ── Single pass ───────────────────────────────────────────────────────────

    async def _single_pass(self, pipeline: dict) -> dict:
        pid = pipeline["id"]
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        await create_pipeline_run(pid, run_id)

        # Resolve analytics store via IAnalyticsStore interface
        store: IAnalyticsStore | None = _analytics_store_for(
            pipeline.get("analytics_connector_id", "")
        )
        if store:
            await store.create_run(pid, run_id)

        stages = pipeline.get("stages", [])
        ai_stages = [s for s in stages if s.get("actor") == "ai"]
        if not ai_stages:
            self._emit(pid, "warn", "No AI stages configured")
            await finish_pipeline_run(run_id, "completed", {})
            return {"issues_processed": 0}

        # Build stage order (column names for AI stages, ordered by index)
        stage_order = [s["column_name"] for s in ai_stages]

        # Query board
        plugin_id = pipeline["plugin_id"]
        token = await load_secret(plugin_id, "token")
        if not token:
            self._emit(pid, "error", "No auth token for plugin")
            await finish_pipeline_run(run_id, "failed", {})
            return {"error": "no_token"}

        issues = await self._query_board(pipeline, token)
        if not issues:
            self._queue[pid] = []
            await finish_pipeline_run(run_id, "completed", {"processed": 0})
            return {"issues_processed": 0}

        # Sort by pull principle (rightmost AI stage first, then lowest issue number)
        stage_priority = {name: len(stage_order) - i for i, name in enumerate(stage_order)}
        issues.sort(key=lambda iss: (-stage_priority.get(iss.stage, -1), iss.number))

        # Load resume state
        state = await get_pipeline_state(pid)
        max_retries = pipeline.get("max_retries", 3)
        max_issues = pipeline.get("max_issues", 50)

        # Model management
        models = pipeline.get("models", [])
        if models:
            models_sorted = sorted(models, key=lambda m: m.get("priority", 999))
            model_list = [m.get("model", "") for m in models_sorted if m.get("model")]
        else:
            model_list = []
        current_model_idx = 0
        consec_failures = 0

        counts = {"processed": 0, "succeeded": 0, "failed": 0, "skipped": 0}

        # Publish initial queue (actionable issues not yet active)
        def _as_dict(iss: BoardIssue) -> dict:
            return {"number": iss.number, "repo": iss.repo, "stage": iss.stage,
                    "title": iss.title, "labels": iss.labels, "updated_at": iss.updated_at}

        actionable = [
            iss for iss in issues
            if iss.stage in stage_priority
            and state.get(f"{iss.repo}:{iss.number}:{iss.stage}", {}).get("status") != "completed"
            and state.get(f"{iss.repo}:{iss.number}:{iss.stage}", {}).get("attempt_count", 0) < max_retries
        ]
        self._queue[pid] = [_as_dict(i) for i in actionable]
        self._active[pid] = None

        for issue in issues:
            if counts["processed"] >= max_issues:
                break

            # Check if stage is actionable
            if issue.stage not in stage_priority:
                continue

            # Check resume state
            issue_key = f"{issue.repo}:{issue.number}:{issue.stage}"
            st = state.get(issue_key, {})
            if st.get("status") == "completed":
                continue
            if st.get("attempt_count", 0) >= max_retries:
                counts["skipped"] += 1
                continue

            # Find stage config
            stage_cfg = next(
                (s for s in stages if s["column_name"] == issue.stage), None
            )
            if not stage_cfg:
                continue

            # Resolve agent for this stage
            agent_id = stage_cfg.get("agent_id", "")
            agent_data = None
            if agent_id:
                agent_data = await get_agent(agent_id)
            # Fall back to inline prompt if no agent
            if not agent_data and not stage_cfg.get("prompt"):
                continue

            counts["processed"] += 1
            current_model = model_list[current_model_idx] if model_list else ""

            # Mark as active, remove from queue
            self._active[pid] = {**_as_dict(issue), "started_at": datetime.now(timezone.utc).isoformat(), "model": current_model}
            self._queue[pid] = [q for q in self._queue.get(pid, []) if not (q["number"] == issue.number and q["repo"] == issue.repo)]

            agent_label = f" via agent '{agent_id}'" if agent_data else ""
            self._emit(pid, "info",
                f"Processing #{issue.number} ({issue.repo}) at stage '{issue.stage}'"
                + agent_label
                + (f" with {current_model}" if current_model else ""),
                issue=issue.number, stage=issue.stage,
            )

            # Render prompt (task_prompt for agents, full prompt for inline)
            if agent_data:
                prompt = self._render_prompt_text(
                    stage_cfg.get("task_prompt", "") or f"Process issue #{issue.number} at stage {issue.stage}",
                    issue, pipeline, stage_cfg,
                )
            else:
                prompt = self._render_prompt(stage_cfg, issue, pipeline)

            # Pre-check declared skill dependencies
            if agent_data and agent_data.get("skills"):
                working_dir = pipeline.get("working_dir", "") or ""
                missing = []
                for skill_path in agent_data["skills"]:
                    full = Path(working_dir) / skill_path if working_dir else Path(skill_path)
                    if not full.exists():
                        missing.append(skill_path)
                if missing:
                    self._emit(pid, "warning",
                        f"⚠ #{issue.number}: agent '{agent_id}' requires missing skills: {', '.join(missing)}. "
                        f"Ensure working_dir is set and the skills/ directory is mounted.",
                        issue=issue.number, stage=issue.stage,
                    )

            # Write agent file if using custom agent
            if agent_data:
                self._write_agent_file(agent_data)

            # Execute session
            started_at = datetime.now(timezone.utc).isoformat()
            result = await self._run_session(
                pipeline, issue, prompt, current_model,
                agent_name=agent_id if agent_data else "",
                token=token,
            )
            ended_at = datetime.now(timezone.utc).isoformat()

            # Move issue on board based on on_success / on_failure stage config
            on_success_col = stage_cfg.get("on_success", "") or ""
            on_failure_col = stage_cfg.get("on_failure", "") or ""
            on_failure_label = stage_cfg.get("on_failure_label", "") or ""

            if result.exit_code == 0 and not result.timed_out and on_success_col:
                moved = await self._move_issue(pipeline, issue, on_success_col, token)
                if moved:
                    result.stage_changed = True
                    result.new_stage = on_success_col
                else:
                    result.exit_code = 1  # move failed → treat as failure
            elif (result.exit_code != 0 or result.timed_out):
                if on_failure_label:
                    await self._add_label_to_issue(pipeline, issue, on_failure_label, token)
                if on_failure_col:
                    await self._move_issue(pipeline, issue, on_failure_col, token)
            elif result.exit_code == 0 and not result.timed_out and not on_success_col:
                # No on_success configured — fall back to polling GitHub for progression
                result = await self._check_progression(pipeline, issue, token, result)

            # Update state
            attempt_count = st.get("attempt_count", 0) + 1
            self._active[pid] = None  # clear active after session ends
            if result.exit_code == 0 and result.stage_changed:
                await set_pipeline_state(pid, issue_key, "completed", attempt_count)
                counts["succeeded"] += 1
                consec_failures = 0
                self._emit(pid, "success",
                    f"✓ #{issue.number} progressed"
                    + (f" → {result.new_stage}" if result.new_stage else ""),
                    issue=issue.number, stage=issue.stage,
                )
            else:
                await set_pipeline_state(pid, issue_key, "attempted", attempt_count)
                counts["failed"] += 1
                consec_failures += 1
                reason = "timed out" if result.timed_out else "failed"
                self._emit(pid, "warn",
                    f"✕ #{issue.number} {reason} (attempt {attempt_count}/{max_retries})",
                    issue=issue.number, stage=issue.stage,
                )

                # Model fallback
                if model_list and consec_failures >= 3 and current_model_idx < len(model_list) - 1:
                    current_model_idx += 1
                    consec_failures = 0
                    self._emit(pid, "warn",
                        f"3 consecutive failures — switching to {model_list[current_model_idx]}")

            # Record run item — SQLite (legacy) + analytics store via IAnalyticsStore
            item = {
                "issue_number": issue.number,
                "issue_repo": issue.repo,
                "issue_title": issue.title,
                "stage": issue.stage,
                "status": "success" if (result.exit_code == 0 and result.stage_changed) else
                          "timeout" if result.timed_out else "failure",
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_s": result.duration_s,
                "model": current_model or None,
                "session_id": result.session_id or None,
            }
            await add_pipeline_run_item(run_id, item)  # SQLite (legacy)
            if store:
                await store.write_run_item(run_id, item, result.shutdown_data)
                await store.write_session_logs(
                    run_id, issue.number, issue.stage, result.output_lines
                )

        final_counts = {**counts, "model": model_list[current_model_idx] if model_list else None}
        await finish_pipeline_run(run_id, "completed", final_counts)
        if store:
            await store.finish_run(run_id, "completed", final_counts)
        self._emit(pid, "info",
            f"Pass complete: {counts['succeeded']} succeeded, "
            f"{counts['failed']} failed, {counts['skipped']} skipped")
        return counts

    # ── Board query ───────────────────────────────────────────────────────────

    async def _query_board(self, pipeline: dict, token: str) -> list[BoardIssue]:
        """Query GitHub Projects v2 board for open issues with their statuses."""
        pid = pipeline["id"]
        owner = pipeline.get("project_owner", "")
        number = pipeline.get("project_number", 0)
        if not owner or not number:
            return []

        plugin_id = pipeline["plugin_id"]
        gh_host = _gh_host_for(plugin_id)

        entity = await _detect_entity_type(gh_host, owner, token)

        gql_url = (
            f"https://{gh_host}/api/graphql"
            if gh_host != "github.com"
            else "https://api.github.com/graphql"
        )

        query = """
        query($cursor: String) {
          %s(login: "%s") {
            projectV2(number: %d) {
              items(first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  content {
                    ... on Issue {
                      number title state updatedAt
                      repository { nameWithOwner }
                      labels(first: 10) { nodes { name } }
                    }
                  }
                  fieldValues(first: 10) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2SingleSelectField { name } }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """ % (entity, owner, number)

        issues: list[BoardIssue] = []
        cursor = None
        allowed_repos = set(pipeline.get("allowed_repos", []))

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                variables = {"cursor": cursor}
                resp = await client.post(
                    gql_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "variables": variables},
                )
                if resp.status_code != 200:
                    self._emit(pid, "error", f"GraphQL returned {resp.status_code}")
                    break

                data = resp.json()
                if data.get("errors"):
                    self._emit(pid, "error", f"GraphQL error: {data['errors'][0].get('message')}")
                    break

                ent_data = data.get("data", {}).get(entity, {})
                project = ent_data.get("projectV2", {})
                items_data = project.get("items", {})

                for node in items_data.get("nodes", []):
                    content = node.get("content")
                    if not content or content.get("state") != "OPEN":
                        continue

                    # Check for watcher-lock label
                    labels = [l["name"] for l in content.get("labels", {}).get("nodes", [])]
                    if "watcher-lock" in labels:
                        continue

                    repo = content.get("repository", {}).get("nameWithOwner", "")
                    if allowed_repos and repo not in allowed_repos:
                        continue

                    # Extract Status field
                    stage = ""
                    for fv in node.get("fieldValues", {}).get("nodes", []):
                        if fv.get("field", {}).get("name") == "Status" and fv.get("name"):
                            stage = fv["name"]
                            break

                    if stage:
                        issues.append(BoardIssue(
                            number=content["number"],
                            repo=repo,
                            stage=stage,
                            title=content.get("title", ""),
                            labels=labels,
                            updated_at=content.get("updatedAt", ""),
                        ))

                page_info = items_data.get("pageInfo", {})
                if page_info.get("hasNextPage"):
                    cursor = page_info["endCursor"]
                else:
                    break

        self._emit(pid, "info", f"Board query returned {len(issues)} open issues")
        return issues

    # ── Prompt rendering ──────────────────────────────────────────────────────

    def _render_prompt(self, stage_cfg: dict, issue: BoardIssue, pipeline: dict) -> str:
        """Render stage prompt template with variable substitution."""
        template = stage_cfg.get("prompt", "")
        if not template:
            return ""

        env_vars = {
            "ISSUE_NUM": str(issue.number),
            "REPO": pipeline.get("project_owner", "") + "/" + str(pipeline.get("project_number", "")),
            "ISSUE_REPO": issue.repo,
            "ISSUE_STAGE": issue.stage,
            "GH_HOST": _gh_host_for(pipeline.get("plugin_id", "")),
            "PROJECT_NUM": str(pipeline.get("project_number", "")),
            "PROJECT_OWNER": pipeline.get("project_owner", ""),
            "PROJECT_ID": "",  # Filled by board query if needed
            "PROJECT_ENTITY": "",
            "ALLOWED_REPOS": " ".join(pipeline.get("allowed_repos", [])),
        }

        # Merge stage-specific env vars
        stage_env = stage_cfg.get("env", {})
        if isinstance(stage_env, str):
            try:
                stage_env = json.loads(stage_env)
            except (json.JSONDecodeError, TypeError):
                stage_env = {}
        env_vars.update(stage_env)

        # Simple ${VAR} substitution (matching envsubst behavior)
        result = template
        for key, value in env_vars.items():
            result = result.replace(f"${{{key}}}", str(value))

        return result

    def _render_prompt_text(
        self, template: str, issue: BoardIssue, pipeline: dict,
        stage_cfg: dict = None,
    ) -> str:
        """Render a prompt template string with variable substitution."""
        if not template:
            return ""
        env_vars = {
            "ISSUE_NUM": str(issue.number),
            "REPO": pipeline.get("project_owner", "") + "/" + str(pipeline.get("project_number", "")),
            "ISSUE_REPO": issue.repo,
            "ISSUE_STAGE": issue.stage,
            "GH_HOST": _gh_host_for(pipeline.get("plugin_id", "")),
            "PROJECT_NUM": str(pipeline.get("project_number", "")),
            "PROJECT_OWNER": pipeline.get("project_owner", ""),
        }
        if stage_cfg:
            stage_env = stage_cfg.get("env", {})
            if isinstance(stage_env, str):
                try:
                    stage_env = json.loads(stage_env)
                except (json.JSONDecodeError, TypeError):
                    stage_env = {}
            env_vars.update(stage_env)

        result = template
        for key, value in env_vars.items():
            result = result.replace(f"${{{key}}}", str(value))
        return result

    def _write_agent_file(self, agent_data: dict) -> None:
        """Write an .agent.md file to ~/.copilot/agents/ for use by Copilot CLI."""
        from ..routers.agents import build_agent_md

        agents_dir = Path.home() / ".copilot" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        content = agent_data.get("agent_md", "")
        if not content:
            content = build_agent_md(agent_data)

        agent_file = agents_dir / f"{agent_data['id']}.agent.md"
        agent_file.write_text(content)

    # ── Session shutdown reader ────────────────────────────────────────────────

    def _read_session_shutdown(self, proc_wall_start: float) -> tuple[str, dict]:
        """Find the Copilot CLI session created after proc_wall_start.

        Scans ~/.copilot/session-state/ for directories whose mtime post-dates
        the subprocess start, reads events.jsonl, and returns
        (session_id, shutdown_data) for the most recent matching session.
        Returns ("", {}) if nothing is found.
        """
        session_root = Path.home() / ".copilot" / "session-state"
        if not session_root.exists():
            return "", {}

        candidates: list[tuple[float, Path]] = []
        try:
            for d in session_root.iterdir():
                if not d.is_dir():
                    continue
                try:
                    mtime = d.stat().st_mtime
                    if mtime >= proc_wall_start:
                        candidates.append((mtime, d))
                except OSError:
                    pass
        except OSError:
            return "", {}

        candidates.sort(reverse=True)  # newest first
        for _, session_dir in candidates:
            events_file = session_dir / "events.jsonl"
            if not events_file.exists():
                continue
            try:
                with events_file.open() as f:
                    for line in f:
                        try:
                            ev = json.loads(line)
                            if ev.get("type") == "session.shutdown":
                                return session_dir.name, ev.get("data", {})
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

        return "", {}

    # ── Session execution ─────────────────────────────────────────────────────

    async def _run_session(
        self, pipeline: dict, issue: BoardIssue, prompt: str, model: str,
        agent_name: str = "", token: str = "",
    ) -> SessionResult:
        """Execute a Copilot session as a subprocess."""
        timeout_hours = pipeline.get("session_timeout_hours", 4.0)
        timeout_secs = int(timeout_hours * 3600)
        plugin_id = pipeline.get("plugin_id", "")
        gh_host = _gh_host_for(plugin_id)
        working_dir = pipeline.get("working_dir") or None

        cmd = ["timeout", str(timeout_secs), "copilot"]
        if model:
            cmd.extend(["--model", model])
        if agent_name:
            cmd.extend(["--agent", agent_name])
        # --yolo = --allow-all-tools --allow-all-paths --allow-all-urls
        # --no-ask-user disables the ask_user tool so agent works autonomously
        cmd.extend(["-p", prompt, "--yolo", "--no-ask-user"])

        # GH_HOST directs gh/copilot to the right GHE instance.
        # Do NOT set GH_TOKEN here: copilot CLI rejects classic PATs (ghp_) and
        # the container already has working Copilot auth via cached credentials.
        # The vault token is still available to gh subprocesses via GH_VAULT_TOKEN
        # if skills/agents need it explicitly.
        env = {**os.environ, "GH_HOST": gh_host}
        if token:
            env["GH_VAULT_TOKEN"] = token  # available to skill scripts, not copilot itself

        start = time.monotonic()
        wall_start = time.time()  # for locating the session-state directory
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=working_dir,
            )

            # Stream output line-by-line so the Boards page can show live thoughts
            _ansi = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
            output_lines: list[str] = []
            async for raw in proc.stdout:  # type: ignore[union-attr]
                line = _ansi.sub('', raw.decode(errors='replace')).rstrip()
                if line.strip():
                    output_lines.append(line)
                    self._emit(
                        pipeline["id"], "session_log", line,
                        issue=issue.number, stage=pipeline.get("current_stage", ""),
                    )

            await proc.wait()
            exit_code = proc.returncode or 0
        except FileNotFoundError:
            self._emit(pipeline["id"], "error", "copilot CLI not found — ensure the Copilot CLI is installed in the container")
            return SessionResult(exit_code=127, duration_s=time.monotonic() - start)
        except Exception as e:
            self._emit(pipeline["id"], "error", f"Session subprocess error: {e}")
            return SessionResult(exit_code=1, duration_s=time.monotonic() - start)

        duration = time.monotonic() - start
        timed_out = exit_code == 124

        output_text = "\n".join(output_lines)
        if exit_code != 0 and not timed_out:
            # Log first 500 chars of output to help diagnose failures
            preview = output_text[:500].strip()
            if preview:
                self._emit(pipeline["id"], "error",
                    f"Session exited {exit_code}: {preview[:200]}")

        # Read session.shutdown from events.jsonl for token/cost data
        session_id, shutdown_data = self._read_session_shutdown(wall_start)
        if shutdown_data:
            model_used = shutdown_data.get("currentModel", "")
            nano_aiu = shutdown_data.get("totalNanoAiu", 0)
            self._emit(
                pipeline["id"], "info",
                f"Session closed: {model_used}, "
                f"{shutdown_data.get('totalPremiumRequests', 0)} premium req, "
                f"{nano_aiu // 1_000_000} µAIU",
                issue=issue.number, stage=pipeline.get("current_stage", ""),
            )

        return SessionResult(
            exit_code=exit_code,
            duration_s=round(duration, 1),
            timed_out=timed_out,
            session_id=session_id,
            shutdown_data=shutdown_data,
            output_lines=output_lines,
        )

    # ── Board move ────────────────────────────────────────────────────────────

    async def _move_issue(
        self, pipeline: dict, issue: BoardIssue, target_column: str, token: str,
    ) -> bool:
        """Move issue to target_column on the GitHub project board via GraphQL.
        Returns True on success, False on failure."""
        gh_host = _gh_host_for(pipeline.get("plugin_id", ""))
        gql_url = (
            f"https://{gh_host}/api/graphql"
            if gh_host != "github.com"
            else "https://api.github.com/graphql"
        )
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        project_owner = pipeline.get("project_owner", "")
        project_number = pipeline.get("project_number", 0)
        owner, repo_name = issue.repo.split("/", 1) if "/" in issue.repo else (issue.repo, "")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Step 1: get project item ID and Status field/option IDs
                q = """
                {
                  repository(owner: "%s", name: "%s") {
                    issue(number: %d) { id projectItems(first: 20) {
                      nodes { id project { number }
                        fieldValues(first: 20) { nodes {
                          ... on ProjectV2ItemFieldSingleSelectValue {
                            name field { ... on ProjectV2SingleSelectField { name } }
                          }
                        }}
                      }
                    }}
                  }
                  organization(login: "%s") {
                    projectV2(number: %d) {
                      id
                      field(name: "Status") {
                        ... on ProjectV2SingleSelectField {
                          id options { id name }
                        }
                      }
                    }
                  }
                }
                """ % (owner, repo_name, issue.number, project_owner, project_number)

                resp = await client.post(gql_url, headers=headers, json={"query": q})
                data = resp.json()

                if "errors" in data:
                    logger.warning("_move_issue GQL query error for #%d: %s", issue.number, data["errors"])
                    return False

                # Extract project item ID for this issue
                project_items = (data.get("data", {}).get("repository", {})
                                 .get("issue", {}).get("projectItems", {}).get("nodes", []))
                item_id = None
                for pi in project_items:
                    if pi.get("project", {}).get("number") == project_number:
                        item_id = pi["id"]
                        break

                if not item_id:
                    logger.warning("_move_issue: issue #%d not found in project %d", issue.number, project_number)
                    return False

                # Extract project ID and Status field option ID for target column
                proj = (data.get("data", {}).get("organization", {}).get("projectV2", {}))
                project_id = proj.get("id")
                status_field = proj.get("field", {})
                field_id = status_field.get("id")
                option_id = None
                for opt in status_field.get("options", []):
                    if opt["name"].strip().lower() == target_column.strip().lower():
                        option_id = opt["id"]
                        break

                if not project_id or not field_id or not option_id:
                    logger.warning(
                        "_move_issue: could not resolve project/field/option for column '%s' "
                        "(project_id=%s, field_id=%s, option_id=%s)",
                        target_column, project_id, field_id, option_id,
                    )
                    return False

                # Step 2: move the item
                mutation = """
                mutation {
                  updateProjectV2ItemFieldValue(input: {
                    projectId: "%s"
                    itemId: "%s"
                    fieldId: "%s"
                    value: { singleSelectOptionId: "%s" }
                  }) { projectV2Item { id } }
                }
                """ % (project_id, item_id, field_id, option_id)

                resp2 = await client.post(gql_url, headers=headers, json={"query": mutation})
                data2 = resp2.json()
                if "errors" in data2:
                    logger.warning("_move_issue mutation error for #%d: %s", issue.number, data2["errors"])
                    return False

                self._emit(pipeline["id"], "info",
                    f"Moved #{issue.number} → {target_column}",
                    issue=issue.number, stage=issue.stage)
                return True

        except Exception as e:
            logger.warning("_move_issue failed for #%d: %s", issue.number, e)
            return False

    # ── Label helper ──────────────────────────────────────────────────────────

    async def _add_label_to_issue(
        self, pipeline: dict, issue: "BoardIssue", label: str, token: str,
    ) -> bool:
        """Add a label to the issue via the REST API. Creates the label if missing."""
        gh_host = _gh_host_for(pipeline.get("plugin_id", ""))
        api_base = (
            f"https://{gh_host}/api/v3"
            if gh_host != "github.com"
            else "https://api.github.com"
        )
        owner, repo_name = issue.repo.split("/", 1) if "/" in issue.repo else (issue.repo, "")
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url = f"{api_base}/repos/{owner}/{repo_name}/issues/{issue.number}/labels"
                resp = await client.post(url, headers=headers, json={"labels": [label]})
                if resp.status_code in (200, 201):
                    self._emit(pipeline["id"], "info",
                        f"Labelled #{issue.number} → {label}",
                        issue=issue.number, stage=issue.stage)
                    return True
                logger.warning("_add_label failed %d for #%d: %s",
                               resp.status_code, issue.number, resp.text[:200])
                return False
        except Exception as e:
            logger.warning("_add_label failed for #%d: %s", issue.number, e)
            return False

    # ── Progression detection (fallback when on_success not configured) ────────

    async def _check_progression(
        self, pipeline: dict, issue: BoardIssue, token: str, result: SessionResult,
    ) -> SessionResult:
        """After a successful session, verify the issue actually moved to a new stage."""
        gh_host = _gh_host_for(pipeline.get("plugin_id", ""))
        gql_url = (
            f"https://{gh_host}/api/graphql"
            if gh_host != "github.com"
            else "https://api.github.com/graphql"
        )

        owner, repo_name = issue.repo.split("/", 1) if "/" in issue.repo else (issue.repo, "")
        if not repo_name:
            return result

        query = """
        { repository(owner: "%s", name: "%s") {
            issue(number: %d) {
              projectItems(first: 100) {
                nodes {
                  project { number }
                  fieldValues(first: 10) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2SingleSelectField { name } }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """ % (owner, repo_name, issue.number)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    gql_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"query": query},
                )
            data = resp.json()
            items = (data.get("data", {}).get("repository", {})
                     .get("issue", {}).get("projectItems", {}).get("nodes", []))

            project_number = pipeline.get("project_number", 0)
            for item in items:
                if item.get("project", {}).get("number") == project_number:
                    for fv in item.get("fieldValues", {}).get("nodes", []):
                        if fv.get("field", {}).get("name") == "Status":
                            new_stage = fv.get("name", "")
                            if new_stage and new_stage != issue.stage:
                                result.stage_changed = True
                                result.new_stage = new_stage
                            elif new_stage == issue.stage:
                                # Silent failure — stage didn't change
                                result.exit_code = 1
                            return result
        except Exception as e:
            logger.warning("Progression check failed for #%d: %s", issue.number, e)

        return result

    # ── Logging helper ────────────────────────────────────────────────────────

    def _emit(self, pipeline_id: str, level: str, message: str,
              issue: int = 0, stage: str = ""):
        event = LogEvent(
            level=level, message=message,
            pipeline_id=pipeline_id, issue=issue, stage=stage,
        )
        log_bus.emit(event)
        log_fn = {"error": logger.error, "warn": logger.warning,
                   "success": logger.info}.get(level, logger.info)
        log_fn("[%s] %s", pipeline_id, message)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _detect_entity_type(host: str, owner: str, token: str) -> str:
    api_base = (
        f"https://{host}/api/v3"
        if host != "github.com"
        else "https://api.github.com"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{api_base}/orgs/{owner}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return "organization" if resp.status_code == 200 else "user"
    except Exception:
        return "organization"  # Default assumption
