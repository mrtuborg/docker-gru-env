# Skill System

Skills are reusable, self-contained capability bundles. They live in `skills/<name>/` folders and follow a standard interface. The server discovers them automatically and makes them available to Quick Actions and the pipeline engine.

## Skill folder structure

```
skills/
└── my-skill/
    ├── SKILL.md          # Documentation + description (required)
    ├── run.sh            # Generate/preview entry point (used by Quick Actions Generate)
    ├── create.sh         # Action entry point (used by Quick Actions Publish)
    └── *.sh / *.py       # Supporting scripts (called by run.sh / create.sh)
```

## Entry point convention

| File | Called when | Expected output |
|------|-------------|-----------------|
| `run.sh "$title" "$context"` | Quick Actions → Generate | Markdown body on stdout |
| `create.sh "$title" "$context"` | Quick Actions → Publish (if present) | Markdown summary on stdout |
| (fallback: first `create-*.sh`) | Generate (if `run.sh` missing) | Markdown body |
| (fallback: first `*.sh`) | Generate (last resort) | Markdown body |

## Environment injection

When the server calls a skill script, it injects:
1. **All variables** from the [Environment page](environment.md) (`/api/env/variables`)
2. **All secrets** from the [Environment page](environment.md) (`/api/env/secrets`) — decrypted
3. **Connector tokens** (on Publish only): `GH_TOKEN`, `GH_HOST`
4. **Pipeline context** (on Publish only): `WORKSPACE` (pipeline working dir)

This means skills can rely on `BATCH_SIZE`, `GH_HOST`, inventory paths, etc. without hardcoding them.

## Skill locations

The server searches two directories (workspace takes precedence):

| Location | Writable | Purpose |
|----------|----------|---------|
| `/workspace/skills/` | No (host mount) | Production skills from the workspace repo |
| `~/.copilot/skills/` | Yes | Editable skills installed via the Skills UI |

## Writing a skill

### Minimal example

```bash
#!/usr/bin/env bash
# run.sh — generate a one-liner issue body
echo "## $1"$'\n\n'"${2:-No additional context.}"
```

### Standard pattern

```bash
#!/usr/bin/env bash
# run.sh — preview what will be created
# $1 = title, $2 = context (may contain flags)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-/workspace}"

# Source shared param parser (if you have one)
# source "$SCRIPT_DIR/_parse_params.sh"

# Do work...
cat <<EOF
## ${1}

${2:-}
EOF
```

### create.sh for multi-issue creation

```bash
#!/usr/bin/env bash
# create.sh — actually create issues on GitHub
# Called by Quick Actions Publish when this skill is selected
# Requires: GH_TOKEN, GH_HOST (injected by server)
set -euo pipefail
[[ -z "${GH_TOKEN:-}" ]] && { echo "❌ GH_TOKEN not set" >&2; exit 1; }

gh --hostname "$GH_HOST" issue create \
  --repo "${REPO:-roommate/roommate-sensei-o}" \
  --title "$1" \
  --body "$2"
```

## Built-in skills (workspace)

### `hil-stress` — HIL pipeline orchestration

Helper scripts used by stage agents:

| Script | Called in | Purpose |
|--------|-----------|---------|
| `run.sh "$title" "$context"` | Quick Actions Generate | Dispatches to bug/device/stress template |
| `hil-preflight.sh <N>` | Stage Step 0 | Fetch issue, check needs-human label |
| `hil-read-run-state.sh <N> <STAGE>` | Stage Step 0.5 | Load cross-stage sidecar JSON |
| `hil-needs-human.sh <N> <msg>` | Any stage | Add label + comment + exit 1 |
| `create-stress-issue.sh "$title" "$ctx"` | Quick Actions | Standard HIL batch body |
| `create-stress-full-issue.sh "$title" "$ctx"` | Quick Actions | Full suite batch body |
| `create-bug-report.sh "$title" "$ctx"` | Quick Actions | Bug report body |
| `create-device-issue.sh "$title" "$ctx"` | Quick Actions | Device investigation body |

### `create-stress-run` — Create full stress test run on board

A **create-type skill** that creates the actual issue tree on GitHub:

| Script | Purpose |
|--------|---------|
| `run.sh "$title" "$context"` | Preview: reads inventory, shows batch plan |
| `create.sh "$title" "$context"` | Creates parent + N child issues, adds to project board |
| `_parse_params.sh` | Shared flag parser (sourced, not executed) |

**Flags parsed from context field:**

| Keyword in context | Effect |
|-------------------|--------|
| `quick` | First 6 devices only |
| `full` | Full test suite (+ test 2.5 / 6.4) |
| `serials: 33, 130` | Specific device serials |
| `batch-size: 4` | Custom batch size |
| `inventory: name` | Different inventory file |

## SKILL.md format

The first non-header line of `SKILL.md` becomes the skill's description in the UI.

```markdown
# My Skill

One-line description shown in Quick Actions dropdown and Skills page.

## Scripts
...
```

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills` | List all skills (both locations) |
| `GET` | `/api/skills/{id}` | Get skill metadata + file list |
| `GET` | `/api/skills/{id}/files/{name}` | Read a skill file |
| `PUT` | `/api/skills/{id}/files/{name}` | Write a skill file (copies to writable if needed) |
| `POST` | `/api/skills` | Create new skill in writable dir |
| `DELETE` | `/api/skills/{id}` | Delete skill (writable only) |
| `GET` | `/api/skills/{id}/export` | Download skill as zip |
| `POST` | `/api/skills/import/zip` | Upload skill zip |
| `POST` | `/api/skills/sync/workspace` | Copy all workspace skills to writable |
