# Lessons Learned — docker-gru-env-server

- [2026-06-25] **Azure SAS tokens are user-delegation-capped at 7 days** — `--auth-mode login --as-user` enforces a hard 7-day limit. Account-level SAS requires `listKeys` control-plane permission which most users lack if they only have `Storage Blob Data Contributor`.

- [2026-06-25] **Azure AD device flow requires app registration** — registering an Azure AD app is an admin-only operation in corporate tenants. `az ad app create` fails with "Insufficient privileges" for regular users. The App registrations blade also returns 401.

- [2026-06-25] **`az` CLI credentials are the right Azure auth for gru-server** — mount `~/.azure` from the host (writable, no `:ro`), install `az` CLI in the Docker image, and call `az account get-access-token --resource https://storage.azure.com/`. No app registration, no SAS token expiry, auto-renews with the host `az` session.

- [2026-06-25] **Mount `~/.azure` writable, not read-only** — `AzureCliCredential` and `az account get-access-token` try to write the version-check file and token cache back to `~/.azure`. Mounting `:ro` causes the command to hang or fail. The token cache update is benign; mount it writable.

- [2026-06-25] **`DefaultAzureCredential` hangs in Docker** — without `:ro` fix in place, it tries `SharedTokenCacheCredential` (may open keychain) and `AzureCliCredential` (hangs on read-only write). Using subprocess `az account get-access-token` with a 20s timeout avoids all hanging credential classes.

- [2026-06-25] **Azure plugin availability should be gated on volume mount** — show the Azure plugin card in the wizard only when `/root/.azure` exists. Exposing it unconditionally confuses users who haven't mounted the volume. `GET /api/plugins/types` returns `available: bool` per type; wizard filters accordingly.

- [2026-06-25] **Dashboard shows "Not yet checked" until first health poll** — initial plugin health state is `unknown`. The first `health()` call happens asynchronously after plugin creation. This is expected; the dashboard auto-refreshes. Adding a `asyncio.wait_for(..., timeout=30)` wrapper prevents the health endpoint from hanging indefinitely.
