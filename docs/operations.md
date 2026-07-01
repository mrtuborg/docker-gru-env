# Operations

Day-to-day operational procedures for running, monitoring, and maintaining Gru Server.

## Starting the server

```bash
cd /path/to/docker-gru-env-server
./server-run.sh
```

This builds and starts the `gru-server-dev` container on port 9400. The web UI is at http://localhost:9400.

To restart without rebuilding:
```bash
docker restart gru-server-dev
```

## Hot-deploy (no rebuild)

### Backend only

```bash
docker cp server/routers/my_router.py gru-server-dev:/app/server/routers/my_router.py
docker restart gru-server-dev
```

### Frontend only

```bash
npm --prefix web run build
docker cp server/static/. gru-server-dev:/app/server/static/
```

No restart needed. Refresh the browser.

### Both

```bash
npm --prefix web run build && \
docker cp server/static/. gru-server-dev:/app/server/static/ && \
docker cp server/routers/pipelines.py gru-server-dev:/app/server/routers/pipelines.py && \
docker restart gru-server-dev
```

### Skills (workspace mount)

Skills in `/workspace/` are bind-mounted into the container. Changes to skill scripts take effect immediately — no copy or restart needed.

## Container management

```bash
# Status
docker ps | grep gru-server-dev

# Logs (last 100 lines)
docker logs gru-server-dev --tail 100 --follow

# Shell into container
docker exec -it gru-server-dev bash

# Stop
docker stop gru-server-dev

# Remove (data volume persists)
docker rm gru-server-dev
```

## Ports

| Port | Service |
|------|---------|
| 9400 | HTTP — web UI + API |
| 9401 | (reserved) |

## Connector health check

The Dashboard page shows a health badge per connector. To check health via API:

```bash
curl -s http://localhost:9400/api/connectors | python3 -m json.tool
```

### Re-authenticate a connector

1. Open Connectors page
2. Click the connector
3. Re-enter token → Save

Token is encrypted and stored in `credentials` table.

## Pipeline operations

### Start pipeline

```bash
curl -X POST http://localhost:9400/api/pipelines/hil-stress/start
```

### Stop pipeline

```bash
curl -X POST http://localhost:9400/api/pipelines/hil-stress/stop
```

### View live log

```bash
curl -N http://localhost:9400/api/pipelines/hil-stress/log
# or open in browser: http://localhost:9400 → Boards → click pipeline
```

### Reset stuck issue

If an issue got stuck in a stage, reset its attempt count:

```bash
# From inside container
docker exec gru-server-dev sqlite3 /data/gru/server.db \
  "UPDATE pipeline_state SET attempt_count=0 WHERE issue_key='owner/repo#123';"
```

Or delete the state row to let it retry from scratch:

```bash
docker exec gru-server-dev sqlite3 /data/gru/server.db \
  "DELETE FROM pipeline_state WHERE issue_key='owner/repo#123';"
```

## Backup and restore

### Full backup

```bash
BACKUP_DIR=~/gru-backups/$(date +%Y%m%d)
mkdir -p "$BACKUP_DIR"
docker cp gru-server-dev:/data/gru/server.db "$BACKUP_DIR/server.db"
docker cp gru-server-dev:/data/gru/vault.key "$BACKUP_DIR/vault.key"
docker cp gru-server-dev:/data/gru/env/files/ "$BACKUP_DIR/files/"
echo "Backup complete: $BACKUP_DIR"
```

Keep `vault.key` and `server.db` together. Losing the vault key makes all secrets permanently unreadable.

### Restore

```bash
docker cp "$BACKUP_DIR/server.db" gru-server-dev:/data/gru/server.db
docker cp "$BACKUP_DIR/vault.key" gru-server-dev:/data/gru/vault.key
docker cp "$BACKUP_DIR/files/." gru-server-dev:/data/gru/env/files/
docker restart gru-server-dev
```

## Logs and debugging

### Backend logs

```bash
docker logs gru-server-dev --follow
```

Log format: `LEVEL:module:message`

### SQLite inspection

```bash
docker exec gru-server-dev sqlite3 /data/gru/server.db ".tables"
docker exec gru-server-dev sqlite3 /data/gru/server.db "SELECT * FROM pipelines;"
```

### Skill script debugging

```bash
# Run generate script manually (container)
docker exec -it gru-server-dev bash
cd /workspace/skills/create-stress-run
GH_TOKEN=... GH_HOST=sensio.ghe.com WORKSPACE=/workspace \
  bash run.sh "Batch stress test" "quick, 6 devices"
```

### Check gh CLI authentication

```bash
docker exec gru-server-dev env GH_TOKEN=<pat> gh --hostname sensio.ghe.com auth status
```

## gh Copilot extension

The extension must be installed in the container for the pipeline engine to spawn AI sessions.

### Check if installed

```bash
docker exec gru-server-dev gh extension list
```

### Install (one-time, survives restart but not container removal)

```bash
docker exec gru-server-dev env GH_TOKEN=<pat> gh extension install github/gh-copilot
```

To make this permanent, add to `Dockerfile.server`:

```dockerfile
RUN gh extension install github/gh-copilot
```

## Rebuilding the image

Required when `Dockerfile.server` or `requirements.txt` changes:

```bash
./server-run.sh
```

This rebuilds and recreates the container. The data volume is preserved.

## Environment variable injection (skills)

Environment variables and secrets are injected into skill subprocesses automatically. To verify what a skill would receive:

```bash
curl -s http://localhost:9400/api/env/variables | python3 -m json.tool
curl -s http://localhost:9400/api/env/secrets | python3 -m json.tool  # values masked
```

Connector tokens (`GH_TOKEN`, `GH_HOST`) are injected by the skill invocation layer and override any same-named env variable.

## Updating documentation

Docs live in `docs/` and are plain markdown. To update:

```bash
cd /path/to/docker-gru-env-server
$EDITOR docs/architecture.md
git add docs/
git commit -m "docs: update architecture"
git push
```
