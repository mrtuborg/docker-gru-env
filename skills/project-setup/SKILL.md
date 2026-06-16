---
name: project-setup
description: Creates a new GitHub Projects v2 board for a repo following the team's standard
             schema (Status/Priority/Complexity/Date fields with all standard options), then
             queries all IDs and writes the platform-docs/project-board.md section ready for use.
             Use when setting up a new project board for a repo.
---

# Agent: Project Setup

Creates a GitHub Projects v2 board with the team's standard schema, queries back all IDs,
and outputs a `platform-docs/project-board.md` section ready to paste or write directly.

---

## Step 1 — Gather inputs

Ask the user for:
- **GHE host** (e.g. `github.com` — default if in a known repo)
- **Organization** (e.g. `custom-repo`)
- **Project title** (e.g. `OpenThread Border Router Hub (Guro)`)
- **Epic issue number** (e.g. `#46`) — or `none`
- **When to use** description — one line explaining what kind of work goes here

Detect host and org from `git remote get-url origin` if possible; confirm with the user.

---

## Step 2 — Get organisation node ID

```bash
GH_HOST=<host> gh api graphql -f query='
{ organization(login:"<org>") { id } }' \
  --jq '.data.organization.id'
```

Save as `ORG_ID`.

---

## Step 3 — Create the project

```bash
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2(input: {
    ownerId: "<ORG_ID>"
    title: "<project-title>"
  }) {
    projectV2 { id number url }
  }
}' --jq '.data.createProjectV2.projectV2'
```

Save `PROJECT_ID`, `PROJECT_NUMBER`, `PROJECT_URL`.

---

## Step 4 — Create Status field

The project starts empty (no Status field). Create it with all standard options:

```bash
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2Field(input: {
    projectId: "<PROJECT_ID>"
    dataType: SINGLE_SELECT
    name: "Status"
    singleSelectOptions: [
      {name: "Backlog",     color: GRAY,   description: "Created, not yet ready"}
      {name: "Todo",        color: BLUE,   description: "Ready — agent may pick up (human-only)"}
      {name: "In Progress", color: YELLOW, description: "Agent is actively working"}
      {name: "On Hold",     color: ORANGE, description: "Paused (human-only)"}
      {name: "HW-Test",     color: PINK,   description: "Deployed to device, running tests (human-only)"}
      {name: "Integration", color: TEAL,   description: "All sibling issues ready, running combined tests (human-only)"}
      {name: "Review",      color: PURPLE, description: "Work done, awaiting human review"}
      {name: "Done",        color: GREEN,  description: "Closed and merged (human-only)"}
    ]
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id name
        options { id name }
      }
    }
  }
}'
```

> **Note**: `TEAL` may not be supported as an enum value in all GHE versions — if it fails,
> use `BLUE` and the human can change the color in the UI project settings.

Save `STATUS_FIELD_ID` and each option ID.

---

## Step 5 — Create Priority field

```bash
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2Field(input: {
    projectId: "<PROJECT_ID>"
    dataType: SINGLE_SELECT
    name: "Priority"
    singleSelectOptions: [
      {name: "Critical", color: RED,    description: "Blocks the feature or human is waiting"}
      {name: "High",     color: ORANGE, description: "Must be done this sprint"}
      {name: "Medium",   color: YELLOW, description: "Important but not urgent"}
      {name: "Low",      color: GRAY,   description: "Nice to have"}
    ]
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id name
        options { id name }
      }
    }
  }
}'
```

Save `PRIORITY_FIELD_ID` and each option ID.

---

## Step 6 — Create Complexity field

```bash
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2Field(input: {
    projectId: "<PROJECT_ID>"
    dataType: SINGLE_SELECT
    name: "Complexity"
    singleSelectOptions: [
      {name: "XS", color: GREEN,  description: "< 1 hour"}
      {name: "S",  color: BLUE,   description: "~half day"}
      {name: "M",  color: YELLOW, description: "1-2 days"}
      {name: "L",  color: ORANGE, description: "3-5 days"}
      {name: "XL", color: RED,    description: "> 1 week"}
    ]
  }) {
    projectV2Field {
      ... on ProjectV2SingleSelectField {
        id name
        options { id name }
      }
    }
  }
}'
```

Save `COMPLEXITY_FIELD_ID` and each option ID.

---

## Step 7 — Create date fields

```bash
# Start date
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2Field(input: {
    projectId: "<PROJECT_ID>"
    dataType: DATE
    name: "Start date"
  }) {
    projectV2Field { ... on ProjectV2Field { id name } }
  }
}'

# Finish date
GH_HOST=<host> gh api graphql -f query='
mutation {
  createProjectV2Field(input: {
    projectId: "<PROJECT_ID>"
    dataType: DATE
    name: "Finish date"
  }) {
    projectV2Field { ... on ProjectV2Field { id name } }
  }
}'
```

Save `START_DATE_FIELD_ID` and `FINISH_DATE_FIELD_ID`.

---

## Step 8 — Write platform-docs/project-board.md section

Using all collected IDs, write or append a new project section to `platform-docs/project-board.md`.

### Section template

```markdown
## Project #<NUMBER> — <TITLE>

**Project ID**: `<PROJECT_ID>`
**URL**: <PROJECT_URL>
**Epic**: #<EPIC_NUMBER> "<epic title>" (or "none")
**When to use**: <when-to-use description>

### Status field (`<STATUS_FIELD_ID>`)

| Stage       | Option ID    | Human-only |
|-------------|--------------|------------|
| Backlog     | `<option-id>` | ✅        |
| Todo        | `<option-id>` | ✅        |
| In Progress | `<option-id>` |           |
| On Hold     | `<option-id>` | ✅        |
| HW-Test     | `<option-id>` | ✅        |
| Integration | `<option-id>` | ✅        |
| Review      | `<option-id>` |           |
| Done        | `<option-id>` | ✅        |

### Priority field (`<PRIORITY_FIELD_ID>`)

| Priority | Option ID    |
|----------|--------------|
| Critical | `<option-id>` |
| High     | `<option-id>` |
| Medium   | `<option-id>` |
| Low      | `<option-id>` |

### Complexity field (`<COMPLEXITY_FIELD_ID>`)

| Size | Option ID    |
|------|--------------|
| XS   | `<option-id>` |
| S    | `<option-id>` |
| M    | `<option-id>` |
| L    | `<option-id>` |
| XL   | `<option-id>` |

### Date fields

| Field       | Field ID                    |
|-------------|-----------------------------|
| Start date  | `<START_DATE_FIELD_ID>`     |
| Finish date | `<FINISH_DATE_FIELD_ID>`    |
```

Also add the project to the **Known Projects** table at the top of `platform-docs/project-board.md`.

---

## Step 9 — Human follow-up (colors)

Post a checklist for the human to complete in the GitHub UI — option colors set via API
may not match the team convention exactly. Open the project → ⚙️ Settings → Fields → Status:

| Stage       | Required color |
|-------------|----------------|
| Backlog     | ⬜ Grey        |
| Todo        | 🔵 Blue        |
| In Progress | 🟡 Yellow      |
| On Hold     | 🟠 Orange      |
| HW-Test     | 🩷 Pink        |
| Integration | 🩵 Teal        |
| Review      | 🟣 Purple      |
| Done        | 🟢 Green       |

---

## Error handling

- If `createProjectV2Field` fails with `TEAL` color: retry with `BLUE`, note in output that
  Integration needs manual color change to Teal in UI.
- If the project already exists: use `get_project_fields` MCP tool (or the query in
  `platform-docs/project-board.md` API Patterns section) to read existing IDs instead of creating.
- If writing `platform-docs/project-board.md` and the file doesn't exist: create it from the template
  in `platform-docs/project-board.md` of the `custom-repo-copilot-tracker` repo.
