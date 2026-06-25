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
from ..vault import load_secret

logger = logging.getLogger(__name__)


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class BoardIssue:
    number: int
    repo: str
    stage: str
    title: str = ""


@dataclass
class SessionResult:
    exit_code: int
    duration_s: float
    stage_changed: bool = False
    new_stage: str = ""
    timed_out: bool = False
    session_id: str = ""


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

    def is_running(self, pipeline_id: str) -> bool:
        task = self._tasks.get(pipeline_id)
        return task is not None and not task.done()

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

            # Write agent file if using custom agent
            if agent_data:
                self._write_agent_file(agent_data)

            # Execute session
            started_at = datetime.now(timezone.utc).isoformat()
            result = await self._run_session(
                pipeline, issue, prompt, current_model,
                agent_name=agent_id if agent_data else "",
            )
            ended_at = datetime.now(timezone.utc).isoformat()

            # Check stage progression
            if result.exit_code == 0 and not result.timed_out:
                result = await self._check_progression(
                    pipeline, issue, token, result,
                )

            # Update state
            attempt_count = st.get("attempt_count", 0) + 1
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

            # Record run item
            await add_pipeline_run_item(run_id, {
                "issue_number": issue.number,
                "issue_repo": issue.repo,
                "stage": issue.stage,
                "status": "success" if (result.exit_code == 0 and result.stage_changed) else
                          "timeout" if result.timed_out else "failure",
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_s": result.duration_s,
                "model": current_model or None,
                "session_id": result.session_id or None,
            })

        await finish_pipeline_run(run_id, "completed", {
            **counts, "model": model_list[current_model_idx] if model_list else None,
        })
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

        # Detect org vs user
        host = "github.com"  # TODO: read from plugin config
        plugin_id = pipeline["plugin_id"]

        # Get host from plugin config (via app state — not available here, use env)
        gh_host = os.environ.get("GH_HOST", "github.com")

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
                      number title state
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
            "GH_HOST": os.environ.get("GH_HOST", "github.com"),
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
            "GH_HOST": os.environ.get("GH_HOST", "github.com"),
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

    # ── Session execution ─────────────────────────────────────────────────────

    async def _run_session(
        self, pipeline: dict, issue: BoardIssue, prompt: str, model: str,
        agent_name: str = "",
    ) -> SessionResult:
        """Execute a Copilot session as a subprocess."""
        timeout_hours = pipeline.get("session_timeout_hours", 4.0)
        timeout_secs = int(timeout_hours * 3600)
        gh_host = os.environ.get("GH_HOST", "github.com")

        cmd = ["timeout", str(timeout_secs), "gh", "copilot", "--"]
        if model:
            cmd.extend(["--model", model])
        if agent_name:
            cmd.extend(["--agent", agent_name])
        cmd.extend(["-p", prompt, "--yolo", "--no-ask-user"])

        env = {**os.environ, "GH_HOST": gh_host}

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            stdout, _ = await proc.communicate()
            exit_code = proc.returncode or 0
        except FileNotFoundError:
            self._emit(pipeline["id"], "error", "gh CLI not found — is GitHub CLI installed?")
            return SessionResult(exit_code=127, duration_s=time.monotonic() - start)
        except Exception as e:
            self._emit(pipeline["id"], "error", f"Session subprocess error: {e}")
            return SessionResult(exit_code=1, duration_s=time.monotonic() - start)

        duration = time.monotonic() - start
        timed_out = exit_code == 124

        return SessionResult(
            exit_code=exit_code,
            duration_s=round(duration, 1),
            timed_out=timed_out,
        )

    # ── Progression detection ─────────────────────────────────────────────────

    async def _check_progression(
        self, pipeline: dict, issue: BoardIssue, token: str, result: SessionResult,
    ) -> SessionResult:
        """After a successful session, verify the issue actually moved to a new stage."""
        gh_host = os.environ.get("GH_HOST", "github.com")
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
