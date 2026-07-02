# Quick Actions

Quick Actions are on-demand shortcuts displayed in the right column of the **Boards** page. They let you create GitHub issues from templates or trigger automated workflows without leaving the UI.

## Concept

Each Quick Action has:
- A **name** (shown in the panel)
- A **stage** (which board column the created issue starts in)
- A **skill** (optional — a skill folder to generate the body or create the issues)
- A **repo** (target `owner/repo` for issue creation)
- A **labels** list (applied to created issues)

## Workflow

```
User types title + notes
         │
         ▼
[Generate button] → POST /api/quick-actions/{id}/generate
         │
         ├── skill set → runs skill/run.sh "$title" "$notes"
         │               returns Markdown body
         │
         └── no skill → calls GitHub Models LLM API
                        returns generated body
         │
         ▼
User reviews (editable textarea)
         │
         ▼
[Publish button] → POST /api/quick-actions/publish
         │
         ├── skill has create.sh → runs skill/create.sh "$title" "$notes"
         │                         with GH_TOKEN + GH_HOST injected
         │                         may create multiple issues
         │
         └── no create.sh → REST POST /repos/{owner}/{repo}/issues
                            GraphQL: add to project board at target stage
                            GraphQL: set Status field
```

## Input field (thoughts box)

The textarea labelled **"Your thoughts"** feeds `$2` to skill scripts. It supports both natural language and structured flags:

```
v0.2+178.gcaefe50

quick full
serials: 33, 130, 166
batch-size: 3
inventory: testwall-02-south
```

Skills parse this text themselves (see [skills.md](skills.md#create-stress-run)).

## Skill-based vs LLM generation

| | Skill | LLM fallback |
|-|-------|-------------|
| Speed | Instant (local script) | ~2s |
| Consistency | Deterministic, templated | Varies |
| Context | Reads inventory, config | Uses title + stage only |
| Offline | Yes | Requires connector token |
| Multi-issue | Yes (via create.sh) | No |

**Recommendation**: use a skill for anything involving structured data (device lists, firmware versions). Use LLM for free-form bug reports with no template.

## Multi-issue publish (create.sh)

When a Quick Action has a skill with `create.sh`, clicking Publish delegates entirely to that script:

- The server injects `GH_TOKEN`, `GH_HOST`, `WORKSPACE`, and all env variables/secrets
- The script may create any number of issues
- Output (markdown summary with links) is displayed in the result area
- The server does **not** call the REST API — the script handles everything

This is used by the `create-stress-run` skill to create 1 parent + N child batch issues in one click.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/quick-actions` | List all quick actions |
| `POST` | `/api/quick-actions` | Create quick action |
| `PUT` | `/api/quick-actions/{id}` | Update quick action |
| `DELETE` | `/api/quick-actions/{id}` | Delete quick action |
| `POST` | `/api/quick-actions/{id}/generate` | Generate issue body |
| `POST` | `/api/quick-actions/publish` | Publish issue to GitHub |

### Generate request

```json
{
  "pipeline_id": "hil-stress",
  "stage": "Todo",
  "title": "v0.2+178.gcaefe50",
  "extra_context": "quick full\nValidate audio fix",
  "skill": "create-stress-run"
}
```

### Publish request

```json
{
  "pipeline_id": "hil-stress",
  "stage": "Todo",
  "repo": "roommate/roommate-sensei-o",
  "title": "v0.2+178.gcaefe50",
  "body": "## Stress Test Plan...",
  "labels": [],
  "skill": "create-stress-run"
}
```

### Publish response (single issue)

```json
{ "issue_url": "https://sensio.ghe.com/roommate/roommate-sensei-o/issues/123", "issue_number": 123 }
```

### Publish response (skill create.sh)

```json
{ "message": "## ✅ Stress run created: v0.2+178.gcaefe50\n\n...", "source": "skill" }
```

## Database schema

```sql
CREATE TABLE quick_actions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    action_type TEXT NOT NULL DEFAULT 'create_issue',
    pipeline_id TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}'
    -- config_json contains: stage, repo, labels, skill
);
```

## LLM fallback details

When no skill is configured, the server calls the GitHub Models API:

- **Endpoint (GHE):** `https://{gh_host}/api/v3/models/chat/completions`
- **Endpoint (github.com):** `https://models.inference.ai.azure.com/chat/completions`
- **Model:** `gpt-4o-mini`
- **Auth:** connector PAT (Bearer token)
- **Fallback:** if the API call fails, returns a static Markdown template

The system prompt instructs the LLM to write a concise issue body (≤300 words) with description, acceptance criteria, and device/firmware placeholders.
