# Context — docker-gru-env-server

## Track: feature/gru-server — gru-server standalone web UI

**Branch:** `feature/gru-server`
**Repo:** `/Users/vn/ws/platform-development/docker-gru-env-server`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building `gru-server` — a standalone Docker container mode with a React web UI wizard
for configuring and authenticating connectors (GitHub, Azure, Copilot, Obsidian Sync),
plus a pipeline engine that drives the HIL stress-test workflow against GitHub Projects v2.

### Issue Status

No GHE project board. Work tracked via commits on `feature/gru-server`.

Latest HEAD: `3182776` pushed to `origin/feature/gru-server`.

### Needs Human

- **End-to-end Obsidian Sync test** — requires an active Obsidian Sync subscription.
- **End-to-end Copilot connector test** — go through wizard, add GitHub → authorize OAuth → add Copilot → verify health.
- **GitHub App must be re-registered on GHE if deleted** — Authorize button auto-detects deleted app (404 on device flow), clears stale client_id and shows manifest flow again. But user must manually click "Register GitHub App →" then complete device flow. Alternatively: use a classic PAT (recommended for GHE — GitHub App user tokens `ghu_*` return 401 on some GHE versions).

### Device State

- Container: `gru-server-dev` running on port 9400
- Volume: `gru-data` (has seeded hil-stress pipeline + ghe-roommate connector with PAT auth)
- Mount: `~/.azure:/root/.azure`
- Image: built from HEAD of `feature/gru-server`
- Connector `ghe-roommate`: **HEALTHY** (authenticated as @vlad via classic PAT)
- Pipeline `hil-stress`: **stopped** (not yet started), 6 queued issues visible on Boards page
- Run commands:
  ```bash
  cd /Users/vn/ws/platform-development/docker-gru-env-server
  ./server-run.sh                        # start (existing volume)
  ./server-run.sh --fresh                # wipe + restart
  ./server-run.sh --seed hil-stress/config.yml   # seed pipeline config
  ./server-build.sh                      # rebuild image
  ```

### Next Action

**Pipeline Editor page** — the next session should implement a new page at `/#/pipeline-editor`
(or rename the existing `PipelineEditor.tsx` skeleton) that lets users:
1. View the list of pipeline stages (currently stored in DB as `pipeline_stages` rows)
2. Add / edit / reorder stages visually
3. Set per-stage prompt (text area), actor (ai/human), column (GH Project column picker)
4. Import / export pipeline config as YAML (same format as `hil-stress/config.yml`)

Key files:
- `web/src/pages/PipelineEditor.tsx` — skeleton exists, needs full implementation
- `server/routers/pipelines.py` — CRUD endpoints for stages already exist
- `server/db/config.py` — `upsert_pipeline`, `get_pipeline`, `list_pipeline_stages`
- `seed.py` — shows the YAML format used for import

The Boards page now shows queued/active/recent activity correctly.
The Connectors page shows PAT input, OAuth modal handles stale app recovery.

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
| `web/src/pages/Pipelines.tsx` | Option B design: Active/Queued/Recent + inline SSE log |
| `web/src/pages/PipelineEditor.tsx` | Skeleton — next session: full stage editor + import/export |
| `web/src/pages/Wizard.tsx` | Setup wizard — connector cards + auth queue |
| `web/src/components/ConnectorConfigForm.tsx` | Per-connector config forms; GitHub: host + board_url + PAT |
| `web/src/components/OAuthModal.tsx` | Auth modal: checks has_token → shows success; needs_manifest → registration |
| `web/src/pages/Connectors.tsx` | Connector list; PAT saved via `POST /auth/pat` |
| `Dockerfile.server` | Multi-stage build; installs az CLI + gh CLI 2.95 + Node 22 + ob |
| `docker-entrypoint.sh` | Runs seed.py if GRU_SEED=1, then starts server |
| `seed.py` | Seeds DB from YAML config; used by `server-run.sh --seed <file>` |
| `server-run.sh` | `--fresh`, `--rebuild`, `--seed <file>` flags |
| `lessons-learned.md` | Non-obvious discoveries |
