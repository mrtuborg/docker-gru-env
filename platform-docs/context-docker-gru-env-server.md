# Context — docker-gru-env-server

## Track: feature/gru-server — gru-server standalone web UI

**Branch:** `feature/gru-server`
**Repo:** `/Users/vn/ws/platform-development/docker-gru-env-server`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building `gru-server` — a standalone Docker container mode with a React web UI wizard
for configuring and authenticating connectors (GitHub, Azure, Copilot, Obsidian).
Replaces the submodule-based `docker-gru-env` workflow with a browser-only setup.
Note: code internals use "connector" everywhere; DB table + API URLs kept as "plugins" for backward compat.

### Issue Status

No GHE project board. Work tracked via commits on `feature/gru-server`.

### Needs Human

- **End-to-end connector test** — container is fresh (no connectors). Run wizard at `localhost:9400`,
  add GitHub → authorize OAuth → add Copilot (inherits GitHub token automatically) → verify health.

### Device State

- Container: `gru-server-test` running on port 9400
- Volume: `gru-data` (fresh — no connectors configured, wizard will show)
- Mount: `~/.azure:/root/.azure` (writable — required for az CLI token cache)
- Image: `gru-server:latest` (rebuilt 2026-06-25 with gh CLI 2.95 + az CLI)
- Restart command:
  ```bash
  docker rm -f gru-server-test && docker volume rm gru-data && \
  docker run -d --name gru-server-test -p 9400:9400 \
    -v gru-data:/data -v ~/.azure:/root/.azure gru-server:latest
  ```

### Next Action

Next session: rework the Obsidian connector from a file-path-based MD reader into an
**Obsidian Sync connector** — this means connecting to Obsidian Sync (cloud service) instead
of requiring a local mounted directory. Research Obsidian Sync API/approach, propose design,
then implement.

Remaining work on the feature branch:
- Rework Obsidian MD connector → Obsidian Sync connector (next session goal)
- Test Copilot connector health end-to-end after wizard setup
- Open PR: `feature/gru-server` → `main`

---

## Shared

### Connector naming convention

- **UI + Python internals**: "connector" everywhere (`GruConnector`, `ConnectorManager`, `ConnectorConfigForm.tsx`)
- **API URL paths**: `/api/plugins/*` (kept unchanged for backward compat)
- **DB table**: `plugins` (unchanged)
- **JSON key**: `plugin_type` (unchanged)

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
- `gh copilot` extension must be installed post-auth: `gh extension install github/gh-copilot`.

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
| `server/connectors/obsidian_connector.py` | Obsidian connector — file-path MD reader (to be replaced) |
| `server/routers/connectors_api.py` | REST API serving `/api/plugins/*` endpoints |
| `web/src/pages/Wizard.tsx` | Setup wizard — fetches availability, auth queue |
| `web/src/components/ConnectorConfigForm.tsx` | Per-connector config forms |
| `web/src/pages/Dashboard.tsx` | Dashboard with `useLocation().key` for refresh |
| `Dockerfile.server` | Multi-stage build; installs az CLI + gh CLI 2.95 in runtime |
| `lessons-learned.md` | Non-obvious discoveries (Azure auth, UX fixes, connector design) |
