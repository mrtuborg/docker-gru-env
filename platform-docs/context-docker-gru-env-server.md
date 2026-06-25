# Context — docker-gru-env-server

## Track: feature/gru-server — gru-server standalone web UI

**Branch:** `feature/gru-server`
**Repo:** `/Users/vn/ws/platform-development/docker-gru-env-server`
**Active project:** N/A (no GHE project board — tracked in repo directly)

### What this track is

Building `gru-server` — a standalone Docker container mode with a React web UI wizard
for configuring and authenticating plugins (GitHub, Azure, Copilot, Obsidian).
Replaces the submodule-based `docker-gru-env` workflow with a browser-only setup.

### Issue Status

No GHE project board. Work tracked via commits on `feature/gru-server`.

### Needs Human

- **Azure plugin end-to-end test** — container is running on port 9400 with `~/.azure` mounted.
  User needs to go through wizard, add Azure Storage plugin (`rmeswprod` / `artifacts`),
  and verify health shows "Healthy" after the first poll (~30 seconds).

### Device State

- Container: `gru-server-test` running on port 9400
- Volume: `gru-data` (fresh — no plugins configured)
- Mount: `~/.azure:/root/.azure` (writable — required for az CLI token cache)
- Image: `gru-server:latest` (built from `feature/gru-server` HEAD)
- Restart command:
  ```bash
  docker rm -f gru-server-test && docker volume rm gru-data && \
  docker run -d --name gru-server-test -p 9400:9400 \
    -v gru-data:/data -v ~/.azure:/root/.azure gru-server:latest
  ```

### Next Action

Azure plugin is implemented and container is running. Next: verify end-to-end by running
the wizard at `localhost:9400` → add Azure Storage (`rmeswprod` / `artifacts`) → confirm
health shows Healthy. Then push `feature/gru-server` and open a PR to `main`.

Remaining work on the feature branch:
- Test Azure plugin health end-to-end
- Test plugin settings page: confirm settings-phase fields render correctly when editing existing plugin
- Verify `board_url` round-trips correctly on plugin reload (reconstruct URL from `project_owner + project_number + host`)
- Push branch + open PR

---

## Shared

### Azure auth solution (final)

**Problem:** SAS tokens cap at 7 days; Azure AD device flow requires app registration (blocked by IT).

**Solution:** Mount `~/.azure` from host + install `az` CLI in the Docker image.
- Auth: `az account get-access-token --resource https://storage.azure.com/` via subprocess
- 20s subprocess timeout + 30s asyncio wrapper prevents hangs
- Mount **without** `:ro` — `az` needs to write token cache
- Azure plugin card hidden in wizard when `/root/.azure` not present (`GET /api/plugins/types`)

### Build

```bash
cd /Users/vn/ws/platform-development/docker-gru-env-server
docker build -f Dockerfile.server -t gru-server:latest .
```

### GitHub plugin (working)

- GHE host: `sensio.ghe.com`
- Wizard: App Manifest → Device Code Flow (all browser-based)
- Dashboard auto-refreshes on navigation via `useLocation().key` dependency
- Device flow URL opens in new tab (ExternalLink icon)

### Key files

| File | Purpose |
|------|---------|
| `server/plugins/azure_plugin.py` | Azure plugin — az CLI subprocess auth |
| `server/plugins/github_plugin.py` | GitHub plugin — App Manifest + Device Code |
| `server/routers/plugins_api.py` | REST API including `/api/plugins/types` |
| `web/src/pages/Wizard.tsx` | Setup wizard — fetches availability, auth queue |
| `web/src/components/PluginConfigForm.tsx` | Per-plugin config forms |
| `web/src/pages/Dashboard.tsx` | Dashboard with `useLocation().key` for refresh |
| `Dockerfile.server` | Multi-stage build; installs az CLI in runtime image |
| `lessons-learned.md` | Non-obvious discoveries (Azure auth, UX fixes) |
