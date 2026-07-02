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

Latest HEAD: `91868df` — `gru-migrate-analytics` JSON dump fallback for containers without asyncpg.

### Needs Human

- **End-to-end Obsidian Sync test** — requires an active Obsidian Sync subscription.
- **GitHub App must be re-registered on GHE if deleted** — use classic PAT as alternative.
- **Recreate `gru-analytics-db` with 127.0.0.1 bind** — existing container was created with 0.0.0.0 binding. Run `./gru-db stop && ./gru-db start --port 9399` to recreate with secure loopback binding.

### Device State

- `gru-server-dev`: **HEALTHY**, port 9400, up ~38 min
- `gru-analytics-db`: **running**, port 9399 exposed (currently 0.0.0.0 — needs recreate with 127.0.0.1 fix)
- `gru-watcher-roommate-sensei-o-hil-stress`: **running** (22 hrs)
- `gru-watcher-roomboard-linux-dev-support-board`: **running** (6 days)
- Analytics PostgreSQL: **populated** — 10 pipeline runs + 7 run items + 709 host sessions + 233 watcher sessions
- Connector `ghe-roommate`: **HEALTHY** (classic PAT, sensio.ghe.com)
- Connector `analytics-main`: **HEALTHY** (host=localhost → translates to host.docker.internal:9399)

Management scripts (main checkout `/Users/vn/ws/platform-development/docker-gru-env-server`):
```bash
./gru-server status|start|stop|restart|logs|wipe|rebuild [--port PORT]
./gru-db     status|start|stop|restart|logs|wipe|psql    [--port PORT]
./gru-migrate-analytics [--from CONTAINER|host] [--scan-sessions] [--dry-run]
```

### Next Action

**Analytics DB web UI** — build a lightweight web UI for the `gru-analytics-db` PostgreSQL container,
similar to the cost analytics dashboard at:
`https://vladimir-nosenko-vladimir-nosenko-sensio-ghe-com.pages.sensio.ghe.com`
(a GitHub Pages hosted HTML dashboard with session cost breakdowns by issue/model/week).

Next session steps:
1. Fetch and analyse the reference dashboard page (requires GHE auth — user provides screenshots)
2. Collect requirements: what tables/charts to show (session cost, token usage, model breakdown, etc.)
3. Design the web UI — standalone HTML (no framework) served from the DB container, or from gru-server
4. Implement and deploy

Key consideration: decide whether the web UI lives in:
- `gru-analytics-db` container (PostgreSQL-only, needs a web server added), OR
- `gru-server-dev` as an `/analytics` page in the existing React UI (easiest — already has the data)

The analytics data is already in PostgreSQL (read via `analytics_connector.py`).
The Sessions page in the React UI already shows token/cost data per session.

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
