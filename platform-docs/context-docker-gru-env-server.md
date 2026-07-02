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
