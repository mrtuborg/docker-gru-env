# Lessons Learned — docker-gru-env-server

- [2026-06-25] **Azure SAS tokens are user-delegation-capped at 7 days** — `--auth-mode login --as-user` enforces a hard 7-day limit. Account-level SAS requires `listKeys` control-plane permission which most users lack if they only have `Storage Blob Data Contributor`.

- [2026-06-25] **Azure AD device flow requires app registration** — registering an Azure AD app is an admin-only operation in corporate tenants. `az ad app create` fails with "Insufficient privileges" for regular users. The App registrations blade also returns 401.

- [2026-06-25] **`az` CLI credentials are the right Azure auth for gru-server** — mount `~/.azure` from the host (writable, no `:ro`), install `az` CLI in the Docker image, and call `az account get-access-token --resource https://storage.azure.com/`. No app registration, no SAS token expiry, auto-renews with the host `az` session.

- [2026-06-25] **Mount `~/.azure` writable, not read-only** — `AzureCliCredential` and `az account get-access-token` try to write the version-check file and token cache back to `~/.azure`. Mounting `:ro` causes the command to hang or fail. The token cache update is benign; mount it writable.

- [2026-06-25] **`DefaultAzureCredential` hangs in Docker** — without `:ro` fix in place, it tries `SharedTokenCacheCredential` (may open keychain) and `AzureCliCredential` (hangs on read-only write). Using subprocess `az account get-access-token` with a 20s timeout avoids all hanging credential classes.

- [2026-06-25] **Azure plugin availability should be gated on volume mount** — show the Azure plugin card in the wizard only when `/root/.azure` exists. Exposing it unconditionally confuses users who haven't mounted the volume. `GET /api/plugins/types` returns `available: bool` per type; wizard filters accordingly.

- [2026-06-25] **Dashboard shows "Not yet checked" until first health poll** — initial plugin health state is `unknown`. The first `health()` call happens asynchronously after plugin creation. This is expected; the dashboard auto-refreshes. Adding a `asyncio.wait_for(..., timeout=30)` wrapper prevents the health endpoint from hanging indefinitely.

- [2026-06-25] **Copilot connector form had leaked pipeline/watcher fields** — `board_dir`, `watcher_stage_order`, `watcher_poll_interval`, `watcher_max_issues`, `watcher_max_per_issue` were showing in the wizard from an old design. The Copilot connector only needs `github_connector_id`. Strip watcher fields from the connector config; they belong in a future pipeline/watcher feature.

- [2026-06-25] **Wiping gru-data volume is required to re-trigger wizard** — after the wizard completes it sets a `wizard_complete` flag in the DB. Restarting the container without `docker volume rm gru-data` keeps old data and shows the dashboard instead of the wizard. Always include `docker volume rm gru-data` when you need a fresh wizard run for testing.

- [2026-06-25] **Connector rename: only UI/Python internals changed, not API/DB** — API URL paths remain `/api/plugins/*`, DB table stays `plugins`, JSON key stays `plugin_type`. Only Python class names and TypeScript symbols were renamed. Don't confuse the two layers when searching for references.

- [2026-06-25] **Obsidian Sync has an official headless CLI** — `obsidian-headless` (npm package, `ob` command) is the official Obsidian-provided CLI for headless sync. Installed via `npm install -g obsidian-headless`. Supports `ob login`, `ob sync-setup --mode pull-only`, `ob sync`, `ob sync-status`. Auth via email/password or `OBSIDIAN_AUTH_TOKEN` env var. No reverse engineering or file-mount required. Node.js 22+ required in the Docker runtime.

- [2026-06-25] **`Vault` is the right Lucide icon for Obsidian** — Lucide has a `Vault` icon that semantically matches Obsidian's core concept. Use it instead of `FileText` or `BookOpen`. `Diamond` and `Gem` are also available if more geometric styling is needed.

- [2026-06-25] **obsidian-headless ob CLI version 0.0.12 is available** — confirmed installable via npm in the Docker runtime stage (`python:3.12-slim` + nodesource Node 22). `ob --version` returns `0.0.12`. Install after adding nodesource repo.

- [2026-06-25] **The Obsidian Sync connector syncs to `/vault/ob-<id>/` inside the container** — each connector instance gets its own subdirectory under `/vault` (declared as a VOLUME). The board file path in config is relative to the vault root. md_kanban.py reads the synced files directly.

- [2026-06-25] **Connectors are multi-role resource providers, not single-purpose adapters** — GitHub provides board + code + agents + skills + knowledge + config. Obsidian provides board + agents + skills + knowledge + config. Azure provides artifacts. Copilot provides execution. Design pipelines around resource bindings, not a single "plugin_id".

- [2026-06-25] **Obsidian pipelines are read-only triggers** — `ob sync --mode pull-only` means the connector cannot write back to the vault. Obsidian Kanban cards can trigger Copilot sessions, but "done" state must be written elsewhere (GitHub issue comment, Azure blob marker). This is a known limitation until obsidian-headless supports bidirectional sync.

- [2026-06-25] **Config portability design: never export secrets** — the `gru-server.yaml` export format omits all tokens, passwords, and credentials. After import, each connector requires re-auth. This makes the config file safe to commit to a repo or store in an Obsidian vault.

- [2026-06-25] **Pipeline phase 1 implementation is purely additive** — the pipeline engine, CRUD API, and UI were already fully implemented in a prior session. Phase 1 (resource bindings) adds a new `pipeline_bindings` table and capability methods on connectors without touching the engine or UI. Migration is automatic: existing `plugin_id` auto-creates a `board` binding.

- [2026-06-30] **GitHub App user tokens (`ghu_*`) are rejected by some GHE instances** — After completing the GitHub App manifest + device flow, the resulting `ghu_*` token returns 401 on `GET /api/v3/user`. Use a **classic PAT** with `repo,project,read:org` scopes instead. Add a PAT field to the GitHub connector Configure form as an alternative auth path.

- [2026-06-30] **PAT must be saved via `POST /auth/pat`, not `PUT /credentials`** — The correct backend endpoint for storing a PAT directly is `POST /api/plugins/{id}/auth/pat`. A non-existent `PUT /credentials` route was silently failing; the PAT appeared to save but was never stored in the vault.

- [2026-06-30] **OAuthModal should check `has_token` before starting device flow** — If a PAT is already stored, `GET /auth/status` returns `{has_token: true}`. The modal should show success immediately instead of trying device flow (which fails on GHE with the App token).

- [2026-06-30] **Deleted GitHub Apps cause 404 on device flow — clear client_id automatically** — When a GitHub App is deleted on GHE, the stale `app_client_id` remains in vault. The next device flow attempt gets a 404. Backend now detects 404, deletes `app_client_id` + `app_client_secret` from vault, and returns `{needs_manifest: true}` so the frontend auto-shows the re-registration step.

- [2026-06-30] **`boards.py` must parse `board_url` to get project owner/number** — The connector config stores `board_url` (e.g. `https://sensio.ghe.com/orgs/roommate/projects/14`) but `boards.py` was reading `project_owner`/`project_number` fields directly (which don't exist). Use a `_parse_board_url()` helper with regex `/(?:orgs|users)/([^/]+)/projects/(\d+)`.

- [2026-06-30] **GitHub Projects v2 board columns via GraphQL work perfectly with PAT on GHE** — `POST /api/graphql` with `organization.projectV2.field(name:"Status").options` returns all column names. The `organization` vs `user` entity type must be detected first via `GET /api/v3/orgs/{owner}`.

- [2026-06-30] **Boards page should show pipeline activity, not a GH board clone** — The page should fetch `/api/pipelines/{id}/status` and display queued/active/recent items, not replicate the GitHub Projects board columns. The engine's `_query_board()` can be called even when the engine is stopped to populate the queue list for display.

- [2026-07-02] **`gru-db start --port` auto-recreates container on port mismatch** — Docker port mappings are fixed at container creation time. `docker stop` does not remove the container. The fix: inspect `.HostConfig.PortBindings`, and if requested port differs, `docker rm -f` + recreate (keeping volume). This is much better than showing an error.

- [2026-07-02] **`find_shutdown_for_window` must use `events.jsonl` mtime, not directory mtime** — Session directories have their mtime updated by later writes (checkpoints, workspace.yaml) hours or days after the session ends. Using directory mtime for the time window filter causes most sessions to fall outside the window. Always stat `events.jsonl` directly.

- [2026-07-02] **`localhost`/`127.0.0.1` inside Docker container resolves to the container, not the host** — When users configure an analytics connector with host=localhost, they expect to reach their Mac. The fix is to translate loopback addresses to `host.docker.internal` in `_build_url()`. Apply the same translation to env var URLs too for consistency. Also add `--add-host host.docker.internal:host-gateway` to `docker run` for Linux hosts.

- [2026-07-02] **Trust-auth PostgreSQL must bind to 127.0.0.1, not 0.0.0.0** — `POSTGRES_HOST_AUTH_METHOD=trust` combined with `-p PORT:5432` (which defaults to 0.0.0.0) means anyone on the same network can connect as the postgres superuser with no credentials. Always use `-p 127.0.0.1:PORT:5432` when exposing a trust-auth DB port.

- [2026-07-02] **Some older Copilot sessions store tokenDetails as `{'tokenCount': N}` dicts** — The `tokenDetails` format changed between Copilot CLI versions. Older sessions have `{"input": {"tokenCount": 547}}` instead of `{"input": 547}`. Always unwrap dict values before casting to int.

- [2026-07-02] **Host Copilot sessions (966 found) are not pipeline runs — scan separately** — `~/.copilot/session-state/` on the host contains standalone Copilot CLI sessions, not pipeline-managed ones. They have no SQLite `pipeline_run` records. Use `gru-migrate-analytics --from host --scan-sessions` to import them as standalone sessions under `host-sessions` run_id.

- [2026-07-02] **Containers without asyncpg need JSON dump relay via gru-server-dev** — The watcher container (`gru-watcher-*`) doesn't have asyncpg installed. The migration script handles this by: (1) dumping sessions to JSON in the source container (no asyncpg needed), (2) copying JSON to gru-server-dev, (3) importing from there. The relay uses `gru-analytics-db:5432` (Docker network) rather than the exposed host port.
