# copilot-workflow

Per-session AI cost tracking for [GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/use-copilot-agents/use-copilot-cli).
Tracks token usage and USD cost per session, attributes costs to GitHub issues, and renders an HTML dashboard.
Ships a Docker-backed shell environment (`gru`) for running autopilot sessions, board watchers, and cost tools.

Two modes of operation:

| Mode | Entry point | Use case |
|------|------------|----------|
| **Shell environment** (`source ./gru`) | `gru` script — sourced in your terminal | Developers running Copilot sessions interactively |
| **Server** (`gru-server`) | `Dockerfile.server` — standalone web UI + API | Fully self-contained, browser-only setup wizard, no host tools needed |

---

## Quick start — gru-server

### First run (build + start)

```bash
# Build the image
docker build -f Dockerfile.server -t gru-server:latest .

# Start the container
docker run -d \
  --name gru-server \
  -p 9400:9400 \
  -v gru-data:/data \
  -v ~/.azure:/root/.azure \
  gru-server:latest

# Open the web UI
open http://localhost:9400
```

### Day-to-day (start / stop existing container)

```bash
docker start gru-server     # start
docker stop gru-server      # stop
docker logs -f gru-server   # tail logs
```

### Or use docker-compose

```bash
docker compose -f docker-compose.server.yml up -d      # start
docker compose -f docker-compose.server.yml down       # stop
docker compose -f docker-compose.server.yml logs -f    # tail logs
```

### Rebuild after code changes

```bash
docker build -f Dockerfile.server -t gru-server:latest . \
  && docker rm -f gru-server \
  && docker run -d \
       --name gru-server \
       -p 9400:9400 \
       -v gru-data:/data \
       -v ~/.azure:/root/.azure \
       gru-server:latest
```

> **Data volume** `gru-data` persists connector config, pipeline definitions, and the
> encrypted secret vault across container restarts. Drop it with `docker volume rm gru-data`
> to start fresh.
>
> **Azure** `~/.azure` mount is optional — only needed if you use the Azure connector
> with `az login` credentials.

### Test container name

During development a separate test container runs on the same port with its own volume:

```bash
# Start / stop the dev container (keeps main gru-server untouched)
docker start gru-server-test
docker stop  gru-server-test
docker logs -f gru-server-test

# Full rebuild of the test container
docker build -f Dockerfile.server -t gru-server:latest . \
  && docker rm -f gru-server-test \
  && docker volume rm gru-data \
  && docker run -d \
       --name gru-server-test \
       -p 9400:9400 \
       -v gru-data:/data \
       -v ~/.azure:/root/.azure \
       gru-server:latest
```

---

## What it does

- **`src/cost-sync.py`** — called by the `sessionEnd` hook; reads token telemetry from `events.jsonl` and appends one cost record to `~/.copilot/cost-log.jsonl`
- **`src/cost-retrospective.py`** — backfill tool; scans all historical sessions and writes `~/.copilot/cost-log-historical.jsonl`
- **`src/cost-report.py`** — generates a text report or HTML dashboard from merged JSONL logs, grouped by GitHub issue
- **`src/pricing.py`** — USD cost engine; mirrors the TypeScript pricing logic from `devartifex/copilot-cost`
- **`scripts/watcher-run.sh`** — orchestrates multi-issue autopilot runs; each issue gets its own session and cost record
- **`skills/`** — Copilot CLI skills: `issue-start`, `session-handoff`, `cost-report`

## Architecture

```
Copilot CLI session
  │
  ├─ issue-start skill    → writes ~/.copilot/session-state/<id>/issue-refs.json
  │
  ├─ [work happens]
  │
  ├─ session-handoff skill
  │
  └─ /new  ──► sessionEnd hook fires
                  │
                  └─ cost-sync.py
                       ├─ reads events.jsonl (session.shutdown event)
                       ├─ computes USD cost via pricing.py
                       └─ appends to ~/.copilot/cost-log.jsonl

Morning:
  python3 src/cost-report.py --format html   →  docs/cost-dashboard.html
  python3 src/cost-report.py                 →  text report to stdout
```

---

## The `gru` environment

`gru` is a **source-only**, Docker-backed shell environment — modelled on `docker-yocto-env`.

```bash
source ./gru            # build/pull image if needed, register commands
source ./gru --rebuild  # force an image rebuild
```

Sourcing prints **"Welcome to the Gru's Lab"** and registers the plugin commands:
`copilot`, `gh-watch`, `md-watch`, `cost`, `data`.

> It must be **sourced**, not executed — it defines shell functions in your current shell.

### File layout

```
gru                     # entry point (sourced). Sets SCRIPT_DIR, prints banner,
                        #   loads core/, runs _initialize_environment, lists commands.
core/
  config.sh             # all CW_* variables, PROJECT_TOP, GH_TOKEN/GH_HOST bridge
  env_core.sh           # sources common/config/lib/plugin_loader; _initialize_environment
  common.sh             # command_exists, print_*, _cw_quote, gh/copilot bootstrap snippets
  plugin_loader.sh      # register_plugin_command, load_plugins, show_plugin_commands
lib/
  docker_utils.sh       # _ensure_cw_image/_volumes/_dirs, _seed_cw_data,
                        #   _run_cw_docker, _cw_dock, _cw_dock_cmd, _cw_dock_bg,
                        #   _logui_start/stop/status (shared dashboard helpers)
plugins/
  copilot.sh            # copilot {shell|run}
  gh-watch.sh           # gh-watch [DIR] {run|start|stop|status}  (GitHub project board)
  md-watch.sh           # md-watch {run|start|stop|status} <board.md> (Obsidian Kanban)
  cost.sh               # cost {report|link|dashboard}
  data.sh               # data {update|preview}
docker/
  Dockerfile            # the gru:local image
  entrypoint.sh         # used by the autopilot *service* container (clones + runs)
  hooks.json            # sessionEnd cost hook installed into the image
  scripts/
    watch-log-ui.py     # dashboard server (pipeline board + live log stream)
```

The non-env directories (`src/`, `scripts/`, `data/`, `prompts/`, `skills/`, `tests/`)
are the actual Python/shell tooling that the plugins invoke inside the container.

### Boot sequence

1. `gru` resolves `SCRIPT_DIR` (the repo root) and prints the banner.
2. `core/env_core.sh` sources: `common.sh` → `config.sh` → `lib/docker_utils.sh` → `plugin_loader.sh`.
3. `_initialize_environment`:
   - `_ensure_cw_image` — builds `gru:local` from `docker/Dockerfile` if missing (or `FORCE_BUILD=true`);
   - `_ensure_cw_volumes` — creates named volumes `gru-data` and `gru-logs`;
   - `_ensure_cw_dirs` — `mkdir -p data/ docs/` on the host;
   - `_seed_cw_data` — copies committed `data/*.jsonl` + `attributions.db` into the data volume **only if absent**;
   - `load_plugins` — sources every `plugins/*.sh` and calls each `<name>_init`.
4. `show_plugin_commands` prints the registered commands.

### Key configuration (`core/config.sh`)

| Variable | Meaning | Default |
|---|---|---|
| `CW_IMAGE` | Image tag | `gru:local` |
| `CW_DATA_VOLUME` / `CW_LOGS_VOLUME` | Named volumes | `gru-data` / `gru-logs` |
| `PROJECT_TOP` | Repo root. **Derived from `SCRIPT_DIR`**, never `git rev-parse` — correct when used as submodule. | `${SCRIPT_DIR}` |
| `GH_HOST` | GitHub Enterprise host | `github.com` |
| `GH_TOKEN` | Auth token. If unset, **auto-derived from host `gh auth token`** — no extra config needed. | host `gh` token |
| `CW_SSH_PATH` | Host `~/.ssh` (mounted ro) | `${HOME}/.ssh` |
| `CW_CONTAINER_DATA_HOME` | `COPILOT_DATA_HOME` inside the container | `/data/copilot` |
| `CW_CONFIG_FLAG` | `--config <abs path>` if `.gru/config.yml` exists | `--config /tools/gru/.gru/config.yml` |

Override any of these by exporting before `source ./gru`.

### The docker contract

Every container run goes through one helper:

```bash
_run_cw_docker <interactive:true|false> <command-string> [host_workspace_dir]
_cw_dock      <command-string> [host_workspace_dir]   # interactive (-it)
_cw_dock_cmd  <command-string> [host_workspace_dir]   # non-interactive
```

Standard mounts on every run:

| Mount / var | Path in container | Mode |
|---|---|---|
| `PROJECT_TOP` | `/tools/gru` | **ro** (tooling) |
| `PROJECT_TOP/data` | `/tools/gru/data` | rw overlay |
| `PROJECT_TOP/docs` | `/tools/gru/docs` | rw overlay |
| `host_workspace_dir` (if given) | `/workspace` | **rw** (your code) |
| `~/.ssh` | `/root/.ssh` | ro |
| `~/.azure` | `/root/.azure` | ro (auto, if present) |
| `~/.gitconfig` | `/root/.gitconfig` | ro (auto, if present) |
| `gru-data` | `/data/copilot` (`COPILOT_DATA_HOME`) | volume |
| `gru-logs` | `/logs` | volume |
| `GH_TOKEN`, `GH_HOST` | env | — |

**Plugin conventions:**
- Use `_cw_quote "$@"` to safely embed user args in the command string.
- Prefix with `${CW_AUTH_BOOTSTRAP}` so `gh`/`copilot` are authenticated before your command runs.
- Use `_cw_dock` for interactive (TTY), `_cw_dock_cmd` otherwise.

---

## Commands

### `copilot` — Copilot CLI over host code

```bash
copilot shell [--dir PATH]                    # interactive Copilot CLI
copilot run [--dir PATH] "prompt" [args...]   # one non-interactive prompt
```

### `gh-watch` — autopilot over a GitHub project board

```bash
gh-watch [DIR] {run|start|stop|status} [BOARD] [--dir PATH] [--port N] [--dry-run] [extra args...]
```

`DIR` (optional) is the name of a config subdirectory inside the workspace containing a
`config.yml`. It differentiates container names so multiple boards can run simultaneously:

```bash
gh-watch hil-stress start           # daemon + dashboard at http://localhost:9300
gh-watch hil-stress status          # running state, recent logs, dashboard URL
gh-watch hil-stress stop            # stop daemon and dashboard
gh-watch hil-stress run --dry-run   # foreground, no sessions started
```

`run` executes in the foreground. `start` launches a background daemon container and opens
a **live dashboard** in the browser:

- **Pipeline columns** — all issues grouped by stage, polled from GitHub every 30 s
- **Active badge** — current issue # and stage with a spinner
- **Completed this run** — ✓/✗ chips per finished issue
- **Live log** — SSE-streamed docker logs, colour-coded

#### Idle-poll interval

The daemon re-queries the board immediately after finishing each issue. When no actionable
issue is found it sleeps for `poll_interval` seconds, then re-queries.

| Setting | Behaviour |
|---|---|
| `poll_interval: N` (config) | Sleep N seconds when board is idle (default: 300) |
| `--poll-interval N` (CLI, takes precedence) | Same, overrides config |
| `poll_interval: 0` / `--poll-interval 0` | **Single-pass** — exits when board empties |

```yaml
# .gru/config.yml
watcher:
  poll_interval: 300   # 0 = single-pass, default = 300
```

```bash
gh-watch hil-stress run --poll-interval 0   # run once and exit (CI)
gh-watch hil-stress start --poll-interval 60  # daemon, poll every 60 s
```

#### Stage prompts for `gh-watch`

Each board column maps to a prompt file. `watcher-run.sh` uses **two-level resolution**:

1. **Consumer override** — `${watcher.prompts_dir}/${stage}.md` (relative to `/workspace`)
2. **Built-in fallback** — `stage-prompts/${stage}.md` in the docker-gru-env tooling

Prompt files are `envsubst` templates. Available variables: `${ISSUE_NUM}`, `${REPO}`,
`${GH_HOST}`, `${ISSUE_STAGE}`.

#### Parent/epic issues (sub-issue guard)

Issues with **sub-issues** are never processed — they are human-managed trackers.
The guard applies the `human-only` label automatically and skips the issue.

### `md-watch` — autopilot from an Obsidian Kanban markdown board

```bash
md-watch {run|start|stop|status} <board.md> [--dir PATH] [--column NAME] [--port N] [--dry-run] [--apply] [-- copilot-args...]
```

Reads an Obsidian Kanban board (`.md` file) and starts one Copilot session per open card
(`- [ ]`) in the actionable column (default `Todo`). `--apply` marks processed cards done.

```bash
md-watch start my-board.md              # daemon + dashboard at http://localhost:9301
md-watch status my-board.md
md-watch stop my-board.md
md-watch run my-board.md --dry-run      # foreground
```

### `cost` — reporting & attribution

```bash
cost report [--format html] [--output PATH] [flags]   # python3 src/cost-report.py
cost link [--apply] [flags]                            # python3 src/cost-link.py
cost dashboard [--regen-only|--publish-only] [flags]   # scripts/build-dashboard.sh
```

### `data` — mirror JSONL → attributions DB

```bash
data update    # cost-link --apply: upsert attributions into the volume DB
data preview   # dry-run, no DB writes
```

---

## Quick start (standalone)

```bash
git clone https://<your-ghe-host>/<owner>/copilot-workflow ~/tools/gru
cd ~/tools/gru

# Register the sessionEnd hook
./scripts/install-hook.sh

# Install skills
mkdir -p ~/.copilot/skills/{issue-start,session-handoff,cost-report}
cp skills/issue-start/SKILL.md     ~/.copilot/skills/issue-start/
cp skills/session-handoff/SKILL.md ~/.copilot/skills/session-handoff/
cp skills/cost-report/SKILL.md     ~/.copilot/skills/cost-report/

# Backfill historical sessions
python3 src/cost-retrospective.py

# Generate dashboard
python3 src/cost-report.py --format html
open docs/cost-dashboard.html
```

### `COPILOT_DATA_HOME`

All `src/*.py` scripts honour this env var. When set, JSONL logs and session-state are
read from and written to that directory instead of `~/.copilot/`.

```bash
COPILOT_DATA_HOME=/tmp/test-copilot python3 src/cost-report.py --text
COPILOT_DATA_HOME=$PWD/data python3 src/cost-report.py --format html
```

---

## Consumer repo setup

1. Create `.gru/config.yml` in your consumer repo:

   ```yaml
   gh_host: your-ghe-host.example.com   # GitHub Enterprise hostname (or github.com)
   data_repo: owner/your-repo
   pages_repo: owner/pages-repo

   project:
     owner: your-org-or-user
     number: 1

   watcher:
     prompts_dir: stage-prompts   # consumer stage handlers (relative to this file)
     poll_interval: 300           # seconds to sleep when board is idle (0 = single-pass)
     max_issues: 50
   ```

2. Add or override stage handlers:

   ```bash
   mkdir -p .gru/stage-prompts
   cp stage-prompts/Todo.md .gru/stage-prompts/Todo.md
   # create .gru/stage-prompts/HW-Test.md for hardware testing stages
   ```

3. Run `./scripts/install-hook.sh` once to register the `sessionEnd` hook.

4. Source the environment (if using as a submodule):

   ```bash
   git submodule add <copilot-workflow-url> copilot-workflow
   ln -s copilot-workflow/gru gru
   source ./gru
   ```

### Using as a submodule

`PROJECT_TOP` resolves to the submodule checkout (not the parent repo), so the image build
context and all mounts always point at copilot-workflow.

```
consumer-repo/                    HOST
  copilot-workflow/  ← submodule  mounted ro at /tools/gru
  .gru/
    config.yml
    stage-prompts/  ← overrides   accessible at /workspace/.gru/stage-prompts
  (code to edit)                  mounted rw at /workspace
```

---

## Extension model

`copilot-workflow` is a **base class**. Consumer projects override stage prompts and
extend Copilot instructions. The pattern is C++ virtual methods: base provides defaults,
consumer overrides what it needs.

### Three-level hierarchy

```
copilot-workflow/              ← base class
  stage-prompts/Todo.md         generic workflow (read issue, implement, review, handoff)
  stage-prompts/In Progress.md  generic resume workflow

~/ws/platform/                 ← workspace subclass (consumer)
  .gru/config.yml   wires watcher-run to platform prompts
  stage-prompts/Todo.md         overrides base: adds multi-repo paths, context file
                                 locations, branch rules, GHE host
  .github/copilot-instructions.md  platform-level Copilot rules

roomboard-linux/               ← repo subclass (most specific)
  .github/copilot-instructions.md  Yocto/BitBake rules, meta layer layout, branch policy
```

### Stage prompts vs. Copilot instructions

| | Stage prompt (`Todo.md`) | Copilot instructions (`.github/…`) |
|---|---|---|
| Loaded by | `watcher-run.sh` before session starts | Copilot CLI from cwd git root |
| Scope | One issue session | All sessions in that repo |
| Override mechanism | `watcher.prompts_dir` in `.gru/config.yml` | One file per git repo |
| Best for | Workflow steps, context injection | Repo conventions, file layout, safety rules |

### Writing a consumer stage prompt

A consumer `Todo.md` embeds the base workflow as a "super" call:

```markdown
<!-- Consumer preamble -->
You are working in the `~/ws/platform/` multi-repo workspace.
Branch base: kirkstone-dev  (never target kirkstone directly)
GH host:     github.com

<!-- Base workflow (super()) -->
You are working on issue #${ISSUE_NUM} in repo ${REPO} (GH_HOST=${GH_HOST}).
... (rest of base Todo.md embedded here)
```

To add a stage that doesn't exist in the base (e.g. `HW-Test`), simply create
`.gru/stage-prompts/HW-Test.md` — no base change needed.

---

## Watcher runs (direct, without `gru`)

```bash
./scripts/watcher-run.sh --config .gru/config.yml
./scripts/watcher-run.sh --repo owner/repo --host your-ghe.example.com --project 8
```

**Flags:**

| Flag | Description |
|---|---|
| `--config PATH` | Load repo/project/watcher settings from YAML |
| `--dry-run` | Print sessions that would run, do nothing |
| `--log-dir DIR` | Write per-issue + full-run logs to DIR |
| `--poll-interval N` | Sleep N seconds when board is idle (0 = single-pass) |
| `--max N` | Safety cap on total issues processed |
| `--max-per-issue N` | Max retry attempts per issue (prevents retry storms) |
| `--resume` | Skip already-completed issues from a previous run |
| `--working-dir PATH` | Run sessions from PATH (multi-repo workspace support) |

---

## Service container (CI / unattended)

For CI runners or cron jobs without a host environment, use `docker/entrypoint.sh`.
It clones the consumer workspace and runs `watcher-run.sh` fully unattended.

**Required env vars:**

| Variable | Description |
|---|---|
| `GH_TOKEN` | Personal access token with `repo` + `project` scopes |
| `WORKSPACE_REPO` | `owner/repo` slug of the consumer workspace |
| `GH_HOST` | GitHub hostname (default: `github.com`) |
| `LINKED_REPOS` | Space-separated `name=owner/repo` pairs cloned into `/workspace/` |
| `OVERNIGHT_ARGS` | Extra flags passed verbatim to `watcher-run.sh` |

**`docker-compose.yml` (consumer project):**

```yaml
services:
  watcher:
    image: gru:local
    build:
      context: /path/to/copilot-workflow
      dockerfile: docker/Dockerfile
    environment:
      GH_TOKEN: ${GH_TOKEN}
      GH_HOST: github.com
      WORKSPACE_REPO: owner/platform
      LINKED_REPOS: roomboard-linux=owner/roomboard-linux
      OVERNIGHT_ARGS: --project 8
    volumes:
      - gru-data:/data/copilot
      - gru-logs:/logs

volumes:
  gru-data:
  gru-logs:
```

---

## Cost dashboard

```bash
GH_HOST=your.ghe.com ./scripts/build-dashboard.sh
```

Steps: regenerate HTML dashboards → sync `Cost ($)` field on project boards → push to Pages.

| Flag | Description |
|------|-------------|
| `--regen-only` | Regenerate dashboards only, skip publish |
| `--publish-only` | Push existing `docs/` without regenerating |
| `--dry-run` | Print what would happen, write nothing |

### Config fields

```yaml
gh_host: github.com
data_repo: owner/copilot-workflow
pages_repo: owner/owner.github.com

pages:
  branch: main

project:
  owner: my-org
  number: 8

# Normalise renamed/moved repos to canonical names used on project boards
repo_aliases:
  "old-owner/old-repo": "new-owner/new-repo"

# Fallback project for sessions whose issue refs don't match any board issue
repo_projects:
  "owner/platform-workspace": 13
  "owner/daily-dev-tracker": 8
```

---

## Cost attribution pipeline

### Step 1 — Auto-attribution

```bash
GH_HOST=github.com python3 src/cost-link.py          # preview
GH_HOST=github.com python3 src/cost-link.py --apply  # write
```

Strategies (in order): existing `issue_refs` → commit SHA → branch name → issue number in session title.

### Step 2 — Manual attribution

Edit `.gru/manual-attributions.yml`:

```yaml
1899dfe2: 25          # known issue number
298fe3ff:             # known project, unknown issue
  issue: -1
  project: 3
64afe9c8:             # leave blank to keep in Unlinked
```

```bash
python3 src/cost-link-manual.py --apply
```

### Step 3 — Find new unlinked sessions

```bash
./scripts/identify-unlinked.sh          # auto-attribute + append stubs to YAML
./scripts/identify-unlinked.sh --apply  # open editor → preview → apply → rebuild
```

| Flag | Description |
|------|-------------|
| `--no-auto` | Skip auto-attribution |
| `--dry-run` | Print stubs without writing |
| `--min-cost N` | Only surface sessions ≥ N USD (default: 0.01) |

### Step 4 — Sync costs to project boards

Writes per-issue cost totals to a `Cost ($)` field on GitHub Projects V2 (created automatically).

```bash
GH_HOST=github.com python3 src/cost-board-sync.py --project-owner my-org --all-projects
```

Also runs automatically inside `build-dashboard.sh`.

### Full pipeline

```bash
GH_HOST=github.com ./scripts/identify-unlinked.sh
python3 src/cost-link-manual.py --apply
GH_HOST=github.com ./scripts/build-dashboard.sh
```

---

## Data & DB model

- The `sessionEnd` hook (`docker/hooks.json` → `src/cost-sync.py`) writes cost JSONL to
  `COPILOT_DATA_HOME=/data/copilot` (the **data volume**).
- The **attributions DB** lives on the volume (`/data/copilot/attributions.db`) — writable
  because the repo mount is ro.
- `_seed_cw_data` seeds the volume from committed `data/` on first run; never overwrites live data.

Records in `~/.copilot/cost-log.jsonl`:

```json
{
  "schema_version": 1,
  "session_id": "abc123",
  "confidence": "exact",
  "repository": "owner/repo",
  "branch": "feat/my-feature",
  "started_at": "2026-06-01T10:00:00Z",
  "ended_at": "2026-06-01T11:30:00Z",
  "issue_refs": [{"issue": 42, "confidence": "exact"}],
  "model_metrics": {"claude-sonnet-4.6": {"input_tokens": 500000, "output_tokens": 8000}},
  "est_cost_usd": 1.85,
  "total_premium_requests": 12
}
```

---

## Scripts reference

| Script | Description |
|--------|-------------|
| `scripts/build-dashboard.sh` | Regen all dashboards, sync board costs, publish to Pages |
| `scripts/identify-unlinked.sh` | Auto-attribute + find new unlinked sessions |
| `scripts/watcher-run.sh` | Multi-issue autopilot loop |
| `scripts/publish-ghpages.sh` | Push `docs/` to Pages branch only |
| `scripts/install-hook.sh` | Register `sessionEnd` hook |
| `scripts/update-data.sh` | Sync JSONL into `data/` and commit (`--push` to push) |

| Tool | Description |
|------|-------------|
| `src/cost-sync.py` | `sessionEnd` hook — appends one record to JSONL |
| `src/cost-retrospective.py` | Backfill historical sessions to JSONL |
| `src/cost-report.py` | Generate HTML dashboards and text reports |
| `src/cost-link.py` | Auto-attribute sessions via commits/PRs/branches |
| `src/cost-link-manual.py` | Apply hand-written attributions from YAML |
| `src/cost-identify-unlinked.py` | Find unlinked sessions, append stubs to YAML |
| `src/cost-board-sync.py` | Sync per-issue costs to GitHub Projects V2 |
| `src/attributions_db.py` | SQLite attribution store (`attributions.db`) |
| `src/pricing.py` | USD cost engine |

---

## Extending: add a new plugin

1. Create `plugins/<name>.sh`.
2. Register and define:

```bash
hello_init() {
    register_plugin_command "hello" "hello" \
        "Example plugin" \
        "hello [name] - print a greeting from inside the container"
}

hello() {
    local extra
    extra=$(_cw_quote "$@")
    _cw_dock_cmd "${CW_AUTH_BOOTSTRAP}; cd /tools/gru && echo hello${extra}"
}
```

3. `source ./gru` — `load_plugins` auto-discovers it.

---

## The image (`docker/Dockerfile`)

- `ubuntu:24.04` + `python3`/pip (PyYAML), `git`, `curl`, `jq`, `gettext-base`, `openssh-client`, `nodejs`/`npm`, `gh` CLI.
- Installs the standalone **GitHub Copilot CLI** via `npm install -g @github/copilot`.
- Installs a `gh-copilot` **shim** so `gh copilot -- …` (used by `watcher-run.sh`) keeps working.
- `ENV COPILOT_DATA_HOME=/data/copilot`; declares volumes for `/data/copilot`, `/logs`, `/workspace`.

---

## Gru Server (`Dockerfile.server`)

A standalone web UI + API that replaces the submodule workflow with a browser-based
setup wizard, plugin system, and visual pipeline editor. Runs entirely inside a Docker
container — no host CLI tools (`gh`, `az`) required.

### Quick start

```bash
# Build
docker build -f Dockerfile.server -t gru-server:local .

# Run (fresh install starts the setup wizard)
# Add -v ~/.azure:/root/.azure if you want Azure Storage plugin
docker run -d --name gru-server -p 9400:9400 \
  -v gru-data:/data \
  -v ~/.azure:/root/.azure \
  gru-server:local

# Open http://localhost:9400 — the wizard guides you through plugin setup
```

> **Azure Storage plugin** is only shown in the wizard if `/root/.azure` is mounted.
> The `~/.azure` mount must be **writable** (no `:ro`) — `az` updates its token cache on use.

### Architecture

```
┌─────────────────────────────────────────────────┐
│  Browser (React SPA)                            │
│  Dashboard · Plugins · Pipelines · Agents       │
│  Boards · Sessions · Settings                   │
└────────────────┬────────────────────────────────┘
                 │ REST + SSE
┌────────────────▼────────────────────────────────┐
│  FastAPI Server (:9400)                         │
│  ├── Plugin Manager (registry, lifecycle)       │
│  ├── Pipeline Engine (board poll, agent exec)   │
│  ├── Vault (AES-256-GCM encrypted secrets)      │
│  └── SQLite config DB (/data/gru.db)            │
└─────────────────────────────────────────────────┘
```

### Plugin system

| Plugin | Auth method | What it provides |
|--------|------------|------------------|
| **GitHub** | App Manifest Flow → Device Code Flow (browser-only) | Board queries, issue management, Copilot attribution |
| **GitHub Copilot** | Inherits from GitHub plugin | Session execution, cost tracking |
| **Azure Storage** | `az` CLI credentials via mounted `~/.azure` | Blob access for firmware bundles |
| **Obsidian Kanban** | Local file path (mounted volume) | Markdown-based Kanban board watching |

### Authentication

**GitHub (GHE):**
1. **Manifest Flow** — server auto-generates an OAuth App registration form.
   User clicks "Create GitHub App" on GHE → one-click registration.
2. **Device Code Flow** — user enters a code at `https://<ghe>/login/device`.
   Token stored in vault, auto-refreshed.

**Azure Storage:**

Uses your existing `az login` session from the host machine. The `az` CLI is installed
inside the container image and reads credentials from the mounted `~/.azure` directory.

- Auth is **automatic** — no token pasting, no app registration.
- Plugin card is **hidden** in the wizard unless `~/.azure` is mounted.
- Mount must be **writable** (no `:ro`) — `az` updates its token cache on use.
- Token is fetched fresh each health check via `az account get-access-token`.
- Health check has a **30 second timeout** and surfaces `az login` expiry as an error.

Background: SAS tokens are capped at 7 days for user-delegation and require
`listKeys` permission for account-level SAS. Azure AD device flow requires
registering an app in the tenant (restricted by IT policy). The `az` CLI approach
bypasses both limitations — credentials auto-renew with the host `az` session.

### Server image details

- **Multi-stage build:** `node:22-alpine` (React build) → `python:3.12-slim` (runtime)
- **Size:** ~345 MB
- **Data:** `/data` volume — SQLite DB, vault, plugin state
- **Port:** 9400 (configurable via `GRU_PORT`)
- **Health check:** `curl -sf http://localhost:9400/api/wizard/status`

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRU_DATA_DIR` | `/data` | Persistent state directory |
| `GRU_PORT` | `9400` | Server listen port |
| `GRU_SERVER_URL` | `http://localhost:9400` | Public URL (for OAuth redirects) |
| `GRU_VAULT_KEY` | auto-generated | AES-256 encryption key for secrets vault |

### API overview

```
# Wizard
GET  /api/wizard/status            Setup status (needs_setup, plugin_count)
POST /api/wizard/save               Save initial configuration

# Plugins
POST   /api/plugins                 Create plugin
GET    /api/plugins                 List plugins
GET    /api/plugins/{id}            Get plugin details
PUT    /api/plugins/{id}            Update plugin config
DELETE /api/plugins/{id}            Delete plugin
GET    /api/plugins/{id}/health     Plugin health check

# Auth (browser-only OAuth)
GET  /api/plugins/{id}/auth/status              Auth readiness
GET  /api/plugins/{id}/auth/manifest/register   Start GitHub App Manifest Flow
GET  /api/auth/github/manifest-callback         Manifest Flow callback
POST /api/plugins/{id}/auth/device-flow/start   Start Device Code Flow
POST /api/plugins/{id}/auth/device-flow/poll    Poll Device Code Flow

# Pipelines
POST   /api/pipelines              Create pipeline
GET    /api/pipelines              List pipelines
GET    /api/pipelines/{id}         Get pipeline + stages + status
PUT    /api/pipelines/{id}         Update pipeline
DELETE /api/pipelines/{id}         Delete pipeline
POST   /api/pipelines/{id}/start   Start watcher loop
POST   /api/pipelines/{id}/stop    Stop watcher loop
GET    /api/pipelines/{id}/runs    Run history
GET    /api/pipelines/{id}/logs    SSE live log stream

# Agents
POST   /api/agents                 Create agent
GET    /api/agents                 List agents
GET    /api/agents/{id}            Get agent details
PUT    /api/agents/{id}            Update agent
DELETE /api/agents/{id}            Delete agent
POST   /api/agents/import-file     Import from .agent.md file
POST   /api/agents/import-repo     Import from git repository

# Dashboard, Boards, Sessions, Settings
GET  /api/dashboard                Dashboard summary
GET  /api/boards                   Board visualization
GET  /api/sessions                 Session list
GET  /api/settings                 Server settings
PUT  /api/settings                 Update settings
```

### SPA pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Dashboard | Overview: plugin status, pipeline summary, recent activity |
| `/plugins` | Plugins | Plugin cards with health indicators, add/configure |
| `/pipelines` | Pipelines | Pipeline list with status, start/stop, live issue counts |
| `/pipelines/:id/edit` | Pipeline Editor | Visual stage editor with inspector panel |
| `/pipelines/:id/runs` | Pipeline Runs | Run history with per-issue results |
| `/pipelines/:id/logs` | Pipeline Logs | SSE live log viewer |
| `/agents` | Agent Library | Agent list, import from file/repo, inline editor |
| `/boards` | Boards | Board visualization (GitHub Projects) |
| `/sessions` | Sessions | Copilot session tracking |
| `/settings` | Settings | Server configuration, vault management |
| `/wizard` | Setup Wizard | First-run plugin configuration |
| `/auth-callback` | Auth Callback | OAuth redirect handler (manifest flow) |

### Design

The UI follows the same color theme as `gh-watcher` in gru:

- **Dark background:** `#0d1117` (GitHub dark)
- **Cards:** `#161b22` with `#30363d` borders
- **Accent:** `#58a6ff` (blue links and active states)
- **Success/Error:** `#3fb950` / `#f85149`
- **Text:** `#e6edf3` primary, `#8b949e` secondary

Navigation uses a left sidebar with icon + label. A gear icon (⚙) in the sidebar
provides quick access to Settings from any page.

---

## Gotchas & limitations

- **Rebuild after image changes.** Run `source ./gru --rebuild` or `docker rmi gru:local`.
- **macOS Docker Desktop file sharing.** Workspace dirs must be under `$HOME`. `/var/folders` temp dirs produce an empty `/workspace`.
- **`copilot` is a shell function after sourcing.** It shadows any host `copilot` binary. Open a fresh shell or `unset -f copilot` to use the host CLI.
- **zsh.** Supported (`${(%):-%x}` fallback). Uses bashisms (`local -a`, `[[ =~ ]]`) that zsh also accepts.
- **`CW_EXTRA_DOCKER_FLAGS`** — word-split on IFS; paths with spaces break it. Use auto-mounts for `~/.azure`/`~/.gitconfig` instead.

---

## Pricing

Pricing data is read from (in order):
1. `COPILOT_COST_PRICING` env var (path to YAML)
2. `~/.copilot/cost-cache/pricing.yaml`
3. `~/tools/copilot-cost/pricing.snapshot.yaml`
4. `src/pricing.snapshot.yaml` (bundled fallback)

Refresh: `npm run refresh-pricing` in `~/tools/copilot-cost/` (requires [devartifex/copilot-cost](https://github.com/devartifex/copilot-cost)).
