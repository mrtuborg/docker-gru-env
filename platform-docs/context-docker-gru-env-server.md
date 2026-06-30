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
