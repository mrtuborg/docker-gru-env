# Context — docker-gru-env-server

## Track: feature/gru-server — gru-server standalone web UI

**Branch:** `feature/gru-server` (main) / `vladimir-nosenko-pipeline-editor-page` (active worktree)
**Repo:** `/Users/vn/ws/platform-development/docker-gru-env-server`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building `gru-server` — a standalone Docker container mode with a React web UI wizard
for configuring and authenticating connectors (GitHub, Azure, Copilot, Obsidian Sync),
plus a pipeline engine that drives the HIL stress-test workflow against GitHub Projects v2.

### Pipeline Editor — COMPLETE (as of 2026-07-01)

The Pipeline Editor page is fully implemented at `/#/pipelines/:id`:
- **Blueprint view** (default): Stage Flow cards, Agent Roster, Shared Tools bar chart, Pipeline Stats with Running/Paused status dot
- **Edit view**: stage CRUD, up/down reorder, prompt editor with template variable chips, per-stage agent assignment, YAML import (modal, paste/upload), YAML export
- **Top bar**: pipeline selector dropdown, Blueprint/Edit toggle, Start/Pause button (now working — backend bug fixed), Import/Export/Save, Ctrl+S shortcut, inline error banners (no more `alert()`)
- **Pipelines list page** eliminated — `/pipelines` now redirects to first pipeline

### Issue Status

No GHE project board. Work tracked via commits.

Latest commits on `vladimir-nosenko-pipeline-editor-page`:
- `b51867e` fix(pipeline-editor): senior dev + UX review — 8 issues fixed
- `7e99560` refactor(pipelines): remove list page, Start/Stop into Blueprint bar
- `0c7fec0` fix(pipelines): remove extra click to reach Blueprint
- `ee33824` feat(pipeline-editor): Blueprint view — agent roster + shared tools
- `102a2ba` fix(pipeline-editor): 6 bugs + 3 UX improvements from senior/UX review

### Needs Human

- **End-to-end Obsidian Sync test** — requires an active Obsidian Sync subscription.
- **End-to-end Copilot connector test** — go through wizard, add GitHub → authorize OAuth → add Copilot → verify health.
- **GitHub App must be re-registered on GHE if deleted** — use a classic PAT instead (recommended for GHE).

### Device State

- Container: `gru-server-dev` running on port 9400
- Volume: `gru-data` (has seeded `hil-stress` pipeline + `ghe-roommate` connector with PAT auth)
- Mount: `~/.azure:/root/.azure`
- Connector `ghe-roommate`: **HEALTHY** (authenticated as @vlad via classic PAT)
- Pipeline `hil-stress`: **stopped**, 6 queued issues (Todo stage), 0 agents loaded
- Run commands:
  ```bash
  cd /Users/vn/ws/platform-development/docker-gru-env-server
  ./server-run.sh                        # start (existing volume)
  ./server-run.sh --fresh                # wipe + restart
  ./server-build.sh                      # rebuild image
  # Hot-deploy frontend:
  npm --prefix web run build && docker cp server/static/. gru-server-dev:/app/server/static/
  # Hot-deploy backend:
  docker cp server/routers/pipelines.py gru-server-dev:/app/server/routers/pipelines.py && docker restart gru-server-dev
  ```

### Next Action

**First pipeline run — agents and skills bootstrap.**

Goal: make `hil-stress` pipeline actually execute one issue end-to-end via `gh copilot`.

Key facts about the engine:
- `pipeline_engine.py` already handles `agent_id` in stage config: looks up agent from DB → writes `~/.copilot/agents/{id}.agent.md` → runs `gh copilot --agent {id} -p <task_prompt>`
- If no `agent_id`: runs `gh copilot -p <full_prompt>` (inline prompt mode)
- `GET /api/agents` returns `[]` — no agents loaded yet
- Agents can be imported via `POST /api/agents/import/file`, `POST /api/agents/import/upload`, or `POST /api/agents/import/repo`

Reference project at `/Users/vn/ws/roommate-sensei-o/`:
- `hil-stress/stage-prompts/*.md` — plain markdown prompts (Todo, HW-Check, HW-Update, HW-Stress, HW-Log)
- `skills/` — shared shell skill scripts referenced from stage prompts
- These are NOT `.agent.md` files — they are raw prompts for inline use

Approach options for next session:
1. **Inline prompt mode** (quickest): Import stage-prompt content into each `hil-stress` stage's `prompt` field directly (no agents needed). Verify the pipeline can pick and execute an issue.
2. **Agent mode**: Create `.agent.md` wrapper files for each stage prompt, import into agent library, link to stages via `agent_id`.

The pipeline editor's YAML import can load stage prompts via `stages[].prompt` field.

Prerequisite check: verify `gh copilot` is installed inside the container and authenticated.

Key files for next session:
- `server/services/pipeline_engine.py` — `_run_session()` builds the `gh copilot` command; `_write_agent_file()` writes agent to `~/.copilot/agents/`
- `server/routers/agents.py` — agent CRUD + import endpoints
- `/Users/vn/ws/roommate-sensei-o/hil-stress/stage-prompts/` — source prompts to import
- `/Users/vn/ws/roommate-sensei-o/skills/` — shared skills referenced from those prompts
- `hil-stress/config.yml` (in this repo) — seed config with stage_order but no prompts

---

## Track: vladimir-nosenko-analytics-db-web-ui — Analytics DB Web UI

**Branch:** `vladimir-nosenko-analytics-db-web-ui`
**Repo:** `/Users/vn/ws/platform-development/copilot-worktrees/docker-gru-env-server/vladimir-nosenko-improved-spork`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building a standalone web dashboard **inside the `gru-analytics-db` container** (not
`gru-server`) that visualizes Copilot session/cost data stored in PostgreSQL. It is a
pixel-faithful clone of a reference "Copilot Cost" dashboard
(`sensio.ghe.com/vladimir-nosenko/vladimir-nosenko.sensio.ghe.com`), remapped to our
vocabulary: **projects** (was "pipelines") and **sessions** (was "run-items").

### Status — dashboard clone functionally complete, but schema is next to be redesigned

Three iterations landed on `docker/analytics-db/web_server.py` (full rewrite → CSS bug fix →
tilde/confidence fix). CSS/JS/element IDs are now byte-identical to the reference except for
injected data. **However, the user has now asked to redesign the underlying DB schema from
scratch next session** to properly support these dashboards, rather than continuing to bolt
fixes onto the `pipeline_runs`/`pipeline_run_items`/`projects` schema inherited from the
pipeline-engine data model.

Latest commits on this branch:
- `7f34c5e` fix: replicate reference's tilde-prefix cost convention and 3-tier confidence
- `7ad834f` fix: remove duplicate nested `<style>` tags breaking dashboard CSS
- `f96b494` feat: rebuild analytics dashboard to match reference Copilot Cost UI/UX
- `01478ac` feat: analytics dashboard built into gru-analytics-db (not gru-server)

### Issue Status

No GHE project board. Work tracked via commits.

### Needs Human

None currently blocking. **Note:** remote is `github.com/mrtuborg/docker-gru-env` — per
policy, do not push/open PRs on this branch without explicit human approval in-session.

### Device State

- `gru-analytics-db`: running, DB port `127.0.0.1:9399`, web UI port `9398`
  (`http://localhost:9398/`), image rebuilt+recreated 3× this session, data preserved
  each time (949 `pipeline_run_items`, 2 `projects` rows).
- `gru-server-dev`: unaffected, still healthy on port 9400.
- Run/rebuild from **this worktree** (not the main checkout — its `gru-db` script lacks
  `build` support):
  ```bash
  cd /Users/vn/ws/platform-development/copilot-worktrees/docker-gru-env-server/vladimir-nosenko-improved-spork
  ./gru-db build
  docker rm -f gru-analytics-db && ./gru-db start --port 9399 --web-port 9398
  ```

### Next Action

**Redesign the analytics DB schema from scratch** (explicit user request for next session),
purpose-built for these dashboards instead of reusing pipeline-engine tables:

1. Review what the reference dashboard actually needs per page (index: projects list +
   aggregates + 2 pie charts; project page: summary stats, monthly/weekly/yearly combo +
   token charts, per-model stats + 3 pies, top-10-issues ×2, by-repo/branch table, session
   timeline, GitHub-style heatmap) — use `docker/analytics-db/web_server.py`'s `fetch_index()`
   /`fetch_project()` as the authoritative list of required fields/aggregates.
2. Design a clean schema (proposal: `projects`, `sessions` as first-class tables instead of
   `pipeline_runs`/`pipeline_run_items` repurposed via `002-projects.sql`) with explicit
   columns for: cost coverage semantics (needed for the tilde/confidence-tier logic — see
   lessons below), issue/repo/branch attribution, token breakdowns, and model usage —
   designed so confidence tiers (exact/low/unknown) and partial-cost aggregation don't need
   ad-hoc `items_with_cost`/`items_with_premium` COUNT() gymnastics in every query.
   Consider a `session_models` child table (one row per model used in a session) instead of
   only per-session single top-model, to support the reference's multi-model share display
   (`Sonnet 4.6 78% / Opus 4.6 13% / Haiku 4.5 4%`) which we currently simplify to one model.
3. Write a migration path from the current schema (`pipeline_runs`, `pipeline_run_items`,
   `projects`, `gru-migrate-analytics` importer) to the new one — do not lose the 949
   already-imported items.
4. Update `analytics_connector.py` DDL and `gru-migrate-analytics` to target the new schema.
5. Rewrite `fetch_index()`/`fetch_project()` SQL against the new schema; verify dashboard
   output is unchanged (byte-diff against `/tmp/ref-dashboard2` if still present, or re-clone
   the reference repo).
6. Rebuild/redeploy `gru-analytics-db`, verify data integrity, commit.

Key files for next session:
- `docker/analytics-db/web_server.py` — dashboard server; `fetch_index()`/`fetch_project()`
  define exactly what data shapes the new schema must support
- `docker/analytics-db/initdb/002-projects.sql` — current (to-be-replaced) schema addition
- `server/connectors/analytics_connector.py` — mirrors the DDL, needs updating in lockstep
- `gru-migrate-analytics` — importer script, needs updating for new schema
- `gru-db` — container management (`build`/`start --port --web-port`) — use the worktree's
  copy, not the main checkout's older version

---

## Shared

### Connector naming convention

- **UI + Python internals**: "connector" everywhere (`GruConnector`, `ConnectorManager`, `ConnectorConfigForm.tsx`)
- **API URL paths**: `/api/plugins/*` (kept unchanged for backward compat)
- **DB table**: `plugins` (unchanged)
- **JSON key**: `plugin_type` (unchanged)

### Connector summary (all 4 implemented)

| Connector | Auth | Health check | Key config |
|---|---|---|---|
| GitHub | App Manifest → Device Code (GHE OAuth) | GHE API reachable + token valid | host, board_url, data_repo |
| Copilot | Inherits GitHub token → `gh auth login` | gh available + token + gh-copilot extension | github_connector_id |
| Azure | `~/.azure` mount + `az account get-access-token` | az CLI available + token works | storage_account, container |
| Obsidian Sync | `ob login` email+password | ob available + sync-status + board file parseable | email, password, vault_name, board_path |

### Azure auth solution (final)

**Problem:** SAS tokens cap at 7 days; Azure AD device flow requires app registration (blocked by IT).

**Solution:** Mount `~/.azure` from host + install `az` CLI in the Docker image.
- Auth: `az account get-access-token --resource https://storage.azure.com/` via subprocess
- 20s subprocess timeout + 30s asyncio wrapper prevents hangs
- Mount **without** `:ro` — `az` needs to write token cache
- Azure connector card hidden in wizard when `/root/.azure` not present (`GET /api/plugins/types`)

### Copilot connector auth

- No separate login. Reads GitHub token from vault via linked GitHub connector ID (auto-discovers if blank).
- Runs `gh auth login --with-token` inside the container using that token.
- Health check: gh available → token exists → `gh copilot` extension installed (DEGRADED if extension missing, not ERROR).

### Obsidian Sync connector

- Uses official `obsidian-headless` npm package (`ob` CLI, version 0.0.12).
- Auth: `ob login --email ... --password ...`; session cached in `~/.config/obsidian-headless/`.
- Sync: `ob sync-setup --vault <name> --path /vault/ob-<id> --mode pull-only` → `ob sync`.
- Board reading: existing `md_kanban.py` parser reads the synced `.md` file.
- `/vault` declared as a Docker VOLUME; each connector instance uses `/vault/ob-<id>/`.
- Requires Node.js 22 in the runtime image (added via nodesource).

### Build

```bash
cd /Users/vn/ws/platform-development/docker-gru-env-server
docker build -f Dockerfile.server -t gru-server:latest .
```

### GitHub connector (working)

- GHE host: `sensio.ghe.com`
- Wizard: App Manifest → Device Code Flow (all browser-based)
- Dashboard auto-refreshes on navigation via `useLocation().key` dependency
- Device flow URL opens in new tab (ExternalLink icon)

### Key files

| File | Purpose |
|------|---------|
| `server/connectors/azure_connector.py` | Azure connector — az CLI subprocess auth |
| `server/connectors/github_connector.py` | GitHub connector — App Manifest + Device Code + PAT; auto-clears deleted app |
| `server/connectors/copilot_connector.py` | Copilot connector — gh CLI health, token from GitHub vault |
| `server/connectors/obsidian_connector.py` | Obsidian Sync connector — ob CLI, pull-only sync, md_kanban |
| `server/routers/connectors_api.py` | REST API `/api/plugins/*`; PAT stored via `POST /auth/pat` |
| `server/routers/boards.py` | Boards router — `_parse_board_url()` parses owner/number from board_url |
| `server/routers/pipelines.py` | Pipelines API — `/status` fetches GH issues even when engine stopped |
| `server/services/pipeline_engine.py` | Core orchestrator — `_query_board()`, `live_state()`, `_gh_host_for()` |
| `web/src/pages/Boards.tsx` | Activity feed: queued/active/recent per pipeline (not GH board clone) |
| `web/src/pages/PipelineEditor.tsx` | **Full implementation** — Blueprint + Edit modes, YAML import/export, stage CRUD, Ctrl+S |
| `web/src/pages/Pipelines.tsx` | Redirect only — navigates to first pipeline Blueprint (or /pipelines/new) |
| `web/src/pages/Wizard.tsx` | Setup wizard — connector cards + auth queue |
| `web/src/components/ConnectorConfigForm.tsx` | Per-connector config forms; GitHub: host + board_url + PAT |
| `web/src/components/OAuthModal.tsx` | Auth modal: checks has_token → shows success; needs_manifest → registration |
| `web/src/pages/Connectors.tsx` | Connector list; PAT saved via `POST /auth/pat` |
| `Dockerfile.server` | Multi-stage build; installs az CLI + gh CLI 2.95 + Node 22 + ob |
| `docker-entrypoint.sh` | Runs seed.py if GRU_SEED=1, then starts server |
| `seed.py` | Seeds DB from YAML config; used by `server-run.sh --seed <file>` |
| `server-run.sh` | `--fresh`, `--rebuild`, `--seed <file>` flags |
| `lessons-learned.md` | Non-obvious discoveries |
