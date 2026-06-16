---
name: issue-create
description: Creates a GitHub issue with the required 4-section template (Context /
             Acceptance criteria / Key files / Blocked by). Enforces template completeness
             before creating. Use mid-session when work is discovered that won't be done now.
---

# Agent: Issue Create

Creates a properly structured GitHub issue. Refuses to create without acceptance criteria.

---

## Step 1 — Detect repo

```bash
git remote get-url origin
```

Parse GH_HOST, REPO, and ORG. If the work is clearly in a submodule (files under a
submodule path), use the submodule's repo. When in doubt, use the parent repo.

---

## Step 2 — Gather required fields

Infer from context or ask the user:

1. **Title** — imperative verb, one line. E.g. "Add IPV6_MULTIPLE_TABLES to Guro kernel config"
2. **Context** — what a fresh agent needs to know. Current state, why it matters, constraints.
   Enough to start work with no other context.
3. **Acceptance criteria** — at least one specific, verifiable outcome with a test command
   or observable result. Vague criteria ("it works", "tests pass") are rejected — ask for specifics.
4. **Key files** — paths relevant to this work (optional but recommended)
5. **Blocked by** — issue number(s) or "nothing"
6. **Priority** — infer from context: Critical / High / Medium / Low
7. **Complexity** — infer from scope: XS / S / M / L / XL

Do NOT proceed to Step 3 if acceptance criteria is empty or vague.

---

## Step 3 — Build and create

Construct the issue body:

```markdown
## Context
<context text>

## Acceptance criteria
- [ ] <criterion 1 — specific and verifiable>
- [ ] <criterion 2 if any>

## Key files
- `<path>` — <why it's relevant>

## Blocked by
<number or "nothing">
```

Create the issue (no label needed — stage is set on the board):
```bash
GH_HOST=<host> gh issue create \
  --repo <repo> \
  --title "<title>" \
  --body "<body>"
```

---

## Step 3.5 — Set blocked-by relationship

If "Blocked by" is not "nothing", call `addBlockedBy` for each blocking issue **immediately
after creating the issue**. Do not skip this — the text in the issue body alone is not
machine-readable by the GitHub API.

```bash
# Get node IDs (REST endpoint returns node_id directly)
ISSUE_ID=$(GH_HOST=<host> gh api /repos/<repo>/issues/<N> --jq '.node_id')
BLOCKING_ID=$(GH_HOST=<host> gh api /repos/<repo>/issues/<BLOCKING_N> --jq '.node_id')

GH_HOST=<host> gh api graphql -f query="
mutation {
  addBlockedBy(input: { issueId: \"$ISSUE_ID\" blockingIssueId: \"$BLOCKING_ID\" }) {
    clientMutationId
  }
}"
```

Repeat for each blocking issue. Confirm with a short message like:
`✓ #N blocked by #M relationship set`

---

## Step 3.6 — Add to epic (sub-issue)

If the issue belongs to an active epic, add it as a sub-issue immediately. Check
`platform-docs/context-<repo>.md` (`## Active Epics`) for the current active epics. **Choose the epic that
matches the active project** (same project number as in `## Active Work`). If multiple epics
match or none is obvious, ask the user before proceeding.

```bash
EPIC_ID=$(GH_HOST=<host> gh api /repos/<repo>/issues/<EPIC_N> --jq '.node_id')
SUB_ID=$(GH_HOST=<host> gh api /repos/<repo>/issues/<N> --jq '.node_id')

GH_HOST=<host> gh api graphql -f query="
mutation {
  addSubIssue(input: { issueId: \"$EPIC_ID\" subIssueId: \"$SUB_ID\" }) {
    issue { number } subIssue { number }
  }
}"
```

Confirm with: `✓ #N added as sub-issue of epic #EPIC_N`

---

## Step 4 — Add to project board and set fields

Every new issue must be added to the active project board immediately.

```bash
# Add to board
GH_HOST=<host> gh project item-add <PROJECT_NUM> --owner <org> --url "<issue-url>"
```

Then set **three fields** via `updateProjectV2ItemFieldValue` (same mutation, different fieldId).
See `platform-docs/project-board.md` for the API pattern and all field/option IDs:
1. **Status** — Default: Backlog. Use In Progress if starting now.
2. **Priority** — from Step 2 assessment.
3. **Complexity** — from Step 2 assessment.

Print the issue URL, board stage, priority and complexity. Done.

---

## Step 5 — If needs-human

If the issue cannot proceed without human input (decision, hardware, credentials):
1. Add label `needs-human`:
   ```bash
   GH_HOST=<host> gh issue edit <N> --repo <repo> --add-label needs-human
   ```
2. The board stage stays **Backlog** — the human will decide when/if to promote it.
3. Note the issue in the session handoff so the user sees it.
