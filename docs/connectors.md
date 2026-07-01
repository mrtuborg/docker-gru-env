# Connector System

Connectors are the integration layer between Gru Server and external services. Every GitHub API call, every Copilot session, and every secret lookup goes through a named connector.

## Built-in connectors

| Type | Class | Purpose |
|------|-------|---------|
| `github` | `copilot_connector.py` (GHE PAT mode) | GitHub REST + GraphQL, board management, issue creation |
| `copilot` | `CopilotConnector` | `gh copilot session` subprocess execution, cost tracking |
| `azure` | `AzureConnector` | Azure DevOps boards, ADO work items |
| `obsidian` | `ObsidianConnector` | Obsidian Sync vault read/write |

## Connector lifecycle

```
DB row (plugins table)
  → ConnectorManager.load_all()     (startup)
  → connector.__init__(plugin_id, config)
  → connector.configure(config)
  → every 30s: connector.health()   (dashboard badges)
  → on shutdown: connector.teardown()
```

## GitHub connector (Copilot connector)

The primary connector. Used by the pipeline engine for all GitHub API calls.

**Auth options:**
- **Classic PAT** (recommended for GHE): `repo`, `project`, `read:org` scopes
- **OAuth** (github.com): Device flow via `POST /api/auth/github/device`

**Config fields:**

| Field | Description |
|-------|-------------|
| `host` | GitHub hostname (`sensio.ghe.com` or `github.com`) |
| `token` | PAT (stored encrypted in vault) |
| `working_dir` | Default working directory for sessions |
| `watcher_stage_order` | Comma-separated stage names |
| `watcher_poll_interval` | Seconds between board polls (default 300) |
| `watcher_max_issues` | Safety cap per run |

**Health check:** calls `GET /api/v3/user` with the token; checks `gh --version`.

**How the pipeline engine uses it:**
```python
token = await load_secret(plugin_id, "token")
gh_host = _gh_host_for(plugin_id)   # reads connector._config["host"]
# All GraphQL calls: https://{gh_host}/api/graphql
# All REST calls: https://{gh_host}/api/v3/...
```

## Copilot connector

Piggybacks on a GitHub connector for auth. Adds:
- `gh copilot` extension health check
- `working_dir` and `board_dir` for session working directories

**Dependency:** requires a GitHub connector to be configured first. References it by `github_connector_id`.

## Azure connector

Connects to Azure DevOps. Config: `organization`, `project`, `pat` (stored in vault).

Currently used for: reading device bundle download URLs from ADO artifact feeds.

## Obsidian connector

Reads/writes files to an Obsidian vault via Obsidian Sync (requires active subscription).
Config: `vault_path` (local path or sync endpoint).

> **Needs human testing** — end-to-end Obsidian Sync test requires an active subscription.

## Adding a new connector

1. Create `server/connectors/my_connector.py`:

```python
from ..connector_base import GruConnector, ConnectorHealth, HealthStatus

class MyConnector(GruConnector):
    @property
    def connector_type(self) -> str:
        return "my-service"          # unique slug

    @property
    def display_name(self) -> str:
        return "My Service"

    @property
    def description(self) -> str:
        return "Connects to My Service for XYZ"

    @property
    def icon(self) -> str:
        return "Cloud"               # Lucide icon name

    @classmethod
    def config_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "api_url":  {"type": "string", "title": "API URL"},
                "api_key":  {"type": "string", "title": "API Key", "secret": True},
            },
            "required": ["api_url"]
        }

    async def configure(self, config: dict) -> None:
        self._config = config
        # Store secret from config into vault if provided
        if config.get("api_key"):
            from ..vault import store_secret
            await store_secret(self.plugin_id, "api_key", config["api_key"])

    async def health(self) -> ConnectorHealth:
        try:
            # Check your service is reachable
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self._config['api_url']}/health")
            if r.status_code == 200:
                return ConnectorHealth(HealthStatus.HEALTHY, "OK")
            return ConnectorHealth(HealthStatus.DEGRADED, f"HTTP {r.status_code}")
        except Exception as e:
            return ConnectorHealth(HealthStatus.ERROR, str(e))

    async def teardown(self) -> None:
        pass   # close any open connections here
```

2. Register in `connector_manager.py`:
```python
from .connectors.my_connector import MyConnector

_CONNECTOR_TYPES = {
    ...
    "my-service": MyConnector,
}
```

3. The connector now appears in the Connectors page wizard automatically (schema drives the UI form).

## Connector storage

```sql
-- plugins table: one row per installed connector
CREATE TABLE plugins (
    id          TEXT PRIMARY KEY,   -- user-chosen name, e.g. "ghe-roommate"
    plugin_type TEXT NOT NULL,      -- e.g. "github", "copilot"
    config      TEXT NOT NULL,      -- JSON (no secrets)
    enabled     INTEGER DEFAULT 1
);

-- credentials: encrypted secrets, keyed by (plugin_id, key)
CREATE TABLE credentials (
    plugin_id  TEXT NOT NULL,
    key        TEXT NOT NULL,       -- e.g. "token", "api_key"
    value      BLOB NOT NULL,       -- AES-256-GCM encrypted, base64
    PRIMARY KEY (plugin_id, key)
);
```

## Vault encryption

Secrets are encrypted with AES-256-GCM before writing to SQLite.

- **Key**: 256-bit random key generated once at first startup, stored at `/data/gru/vault.key` (base64-encoded)
- **Format**: `base64(nonce[12] || ciphertext)`
- **Functions**: `vault.py:_encrypt(plaintext)` / `_decrypt(blob)` / `store_secret()` / `load_secret()`

> **Critical**: losing `vault.key` means all secrets are unrecoverable. Back it up separately from the database.
