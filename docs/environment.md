# Environment

The Environment page (`/environment`) provides three types of global configuration that are automatically available to all skill scripts and pipeline runs.

## Variables

Plain key-value pairs. Use for non-sensitive configuration that skills need.

**Examples:**
```
BATCH_SIZE       = 6
GH_HOST          = sensio.ghe.com
TESTWALL         = testwall-01-north
INVENTORY_DIR    = /data/gru/env/files
```

**How to use in skills:**
```bash
# Variables are injected as environment variables — just read them directly
BATCH_SIZE="${BATCH_SIZE:-6}"
GH_HOST="${GH_HOST:-sensio.ghe.com}"
```

## Secrets

Same as variables but encrypted at rest. Values are never exposed in the UI after saving.

**Encryption:** AES-256-GCM via the vault (see [connectors.md](connectors.md#vault-encryption))

**Examples:**
```
SSHPASS          = <testwall SSH password>
AZURE_PAT        = <Azure DevOps PAT>
SLACK_WEBHOOK    = https://hooks.slack.com/...
```

**How to use in skills:**
```bash
# Secrets are decrypted and injected the same way as variables
ssh sshpass -p "$SSHPASS" root@device.local
```

## Files

Upload arbitrary files (YAML, CSV, text). Stored at `/data/gru/env/files/<name>` inside the container.

**Use cases:**
- Device inventory lists (`testwall-01-north.yaml`)
- CA certificates
- Device credentials files
- Custom configuration overrides

**How skills access files:**
```bash
INVENTORY_DIR="${INVENTORY_DIR:-/data/gru/env/files}"
INV="$INVENTORY_DIR/testwall-01-north.yaml"
[[ ! -f "$INV" ]] && INV="$WORKSPACE/hil-stress/inventory/testwall-01-north.yaml"
```

The recommended pattern is: check `/data/gru/env/files/` first, fall back to `/workspace/`.

## Injection order

When a skill is called, environment variables are merged in this priority order (later wins):

1. Container system environment (`os.environ`)
2. Environment page **Variables**
3. Environment page **Secrets**
4. Caller-provided overrides (`GH_TOKEN`, `GH_HOST`, `WORKSPACE` — on Publish)

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/env/variables` | List all variables |
| `PUT` | `/api/env/variables/{name}` | Create or update variable |
| `DELETE` | `/api/env/variables/{name}` | Delete variable |
| `GET` | `/api/env/secrets` | List secret names (no values) |
| `PUT` | `/api/env/secrets/{name}` | Create or update secret |
| `DELETE` | `/api/env/secrets/{name}` | Delete secret |
| `GET` | `/api/env/files` | List uploaded files |
| `GET` | `/api/env/files/{name}` | Get file content (text) |
| `POST` | `/api/env/files/upload` | Upload a file |
| `GET` | `/api/env/files/{name}/download` | Download a file |
| `DELETE` | `/api/env/files/{name}` | Delete a file |

## Internal helper

Other parts of the server use `environment.load_env_dict()` to load all variables and decrypted secrets into a `dict[str, str]`:

```python
from .routers.environment import load_env_dict

env_vars = await load_env_dict()
env = {**os.environ, **env_vars, "GH_TOKEN": token}
proc = await asyncio.create_subprocess_exec("bash", script, env=env)
```

## Database schema

```sql
CREATE TABLE env_variables (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    updated_at  TEXT
);

CREATE TABLE env_secrets (
    name        TEXT PRIMARY KEY,
    value       BLOB NOT NULL,      -- AES-256-GCM encrypted
    description TEXT NOT NULL DEFAULT '',
    updated_at  TEXT
);
```

Files are stored on disk at `$GRU_DATA_DIR/env/files/` (default: `~/.gru/env/files/` or `/data/gru/env/files/` in container).
