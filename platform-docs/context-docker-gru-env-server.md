# Context — docker-gru-env-server

## Track: feature/gru-server — gru-server standalone web UI

**Branch:** `feature/gru-server`
**Repo:** `/Users/vn/ws/platform-development/docker-gru-env-server`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building `gru-server` — a standalone Docker container mode with a React web UI wizard
for configuring and authenticating connectors (GitHub, Azure, Copilot, Obsidian Sync).
Replaces the submodule-based `docker-gru-env` workflow with a browser-only setup.
Note: code internals use "connector" everywhere; DB table + API URLs kept as "plugins" for backward compat.

### Issue Status

No GHE project board. Work tracked via commits on `feature/gru-server`.

### Needs Human

- **End-to-end Obsidian Sync test** — requires an active Obsidian Sync subscription. Test wizard:
  add Obsidian Sync connector with email/password/vault_name/board_path and verify health shows Healthy.
- **End-to-end Copilot connector test** — go through wizard, add GitHub → authorize OAuth → add Copilot → verify health.
- **Architecture review** — pipeline-design.md extended with Resource Provider Model + Config Portability spec. Review before Phase 1 implementation begins.

### Device State

- Container: `gru-server-test` running on port 9400
- Volume: `gru-data` (fresh — no connectors configured, wizard will show)
- Mount: `~/.azure:/root/.azure` (writable — required for az CLI token cache)
- Image: `gru-server:latest` (rebuilt 2026-06-25 — includes ob 0.0.12, gh 2.95, az CLI)
- Restart command:
  ```bash
  docker rm -f gru-server-test && docker volume rm gru-data && \
  docker run -d --name gru-server-test -p 9400:9400 \
    -v gru-data:/data -v ~/.azure:/root/.azure gru-server:latest
  ```

### Next Action

Pipeline architecture design is complete (design-only session, no code written).
The full spec is in the session artifact `pipeline-design.md` (1348 lines).

Next session: begin **Phase 1 implementation** of the Resource Binding model:
- Add `pipeline_bindings` DB table + migration
- Implement `provides()` on all 4 connectors
- Implement `list_board_items()` + `list_board_columns()` on GitHub + Obsidian connectors
- Update `Pipeline` dataclass to use `bindings: list[ResourceBinding]`
- Update `GET/PUT /api/pipelines/{id}` API to include bindings

Before coding, read `pipeline-design.md` (session artifact) for the full spec.
The prior pipeline engine, CRUD API, and UI are already implemented — only the
resource binding layer and connector capability methods are new.

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
| `server/connectors/github_connector.py` | GitHub connector — App Manifest + Device Code |
| `server/connectors/copilot_connector.py` | Copilot connector — gh CLI health, token from GitHub vault |
| `server/connectors/obsidian_connector.py` | Obsidian Sync connector — ob CLI, pull-only sync, md_kanban |
| `server/routers/connectors_api.py` | REST API serving `/api/plugins/*` endpoints |
| `web/src/pages/Wizard.tsx` | Setup wizard — connector cards + auth queue |
| `web/src/components/ConnectorConfigForm.tsx` | Per-connector config forms |
| `web/src/pages/Dashboard.tsx` | Dashboard with connector health cards |
| `Dockerfile.server` | Multi-stage build; installs az CLI + gh CLI 2.95 + Node 22 + ob |
| `lessons-learned.md` | Non-obvious discoveries (Azure auth, connector design, obsidian CLI) |
