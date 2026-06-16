---
name: issue-start
description: Session-start ritual. Detects the current repo, lists issues in Todo stage
             on the project board, auto-selects the lowest-numbered one, and activates it
             before starting any work. Use at the start of every session.
---

# Agent: Issue Start

Run this at the start of every session before doing any other work.

---

## Step 1 — Detect repo context

```bash
git remote get-url origin
```

Parse the output:
- SSH: `user@host:owner/repo.git` → GH_HOST=`host`, REPO=`owner/repo`, ORG=`owner`
- HTTPS: `https://host/owner/repo.git` → GH_HOST=`host`, REPO=`owner/repo`, ORG=`owner`

Resolve the per-repo context file:
```bash
REPO_NAME=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' \
  | cut -d/ -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
CONTEXT_FILE="platform-docs/context-${REPO_NAME}.md"
```

Find the active project number from `$CONTEXT_FILE` (look for `## Active Work` → **Active project**).
Save as `PROJECT_NUM`.

---

## Step 2 — List issues in Todo stage

Query the project board directly (not labels — labels are no longer used for workflow):

```bash
GH_HOST=<host> gh api graphql -f query='
{ organization(login:"<org>") { projectV2(number:<PROJECT_NUM>) { items(first:100) {
  nodes {
    id
    content { ... on Issue { number title state url labels(first:10) { nodes { name } } } }
    fieldValues(first:10) { nodes {
      ... on ProjectV2ItemFieldSingleSelectValue {
        name field { ... on ProjectV2SingleSelectField { name } }
      }
    }}
  }
} } } }' \
  --jq '[.data.organization.projectV2.items.nodes[]
    | select(.content.state=="OPEN")
    | select((.content.labels.nodes // []) | map(.name) | any(.[]; . == "human-only") | not)
    | {
        item_id: .id,
        number: .content.number,
        title: .content.title,
        url: .content.url,
        stage: (.fieldValues.nodes[] | select(.field.name=="Status") | .name)
      }
    | select(.stage=="Todo")]
    | sort_by(.number)[]
    | "#\(.number) \(.title)"'
```

Also list any issues with `needs-human` label so the user is aware of blockers:
```bash
GH_HOST=<host> gh issue list --repo <repo> --label needs-human --state open \
  --json number,title --jq '.[] | "⚠️  needs-human: #\(.number) \(.title)"'
```

**Default behaviour:** auto-select the **lowest-numbered** Todo issue and say:
> "I'll work on **#N — \<title\>** unless you'd like a different one."
> Here are all Todo issues: [list]

Wait briefly for redirect. If none arrives, proceed.

**On Hold check:** Before starting work, also query On Hold issues:
```bash
# Same query as above but select(.stage=="On Hold")
```
List them to the user and confirm you will not work on them.

---

## Step 3 — Check for conflicting open PRs

```bash
GH_HOST=<host> gh pr list --repo <repo> --state open \
  --json number,title,headRefName \
  --jq '.[] | "#\(.number) [\(.headRefName)] \(.title)"'
```

Flag any PR that clearly overlaps with the chosen issue before proceeding.

---

## Step 4 — Activate the issue

1. Read the full issue body and all comments:
   ```bash
   GH_HOST=<host> gh issue view <N> --repo <repo> --comments
   ```
   Comments often contain human decisions that override or refine the original body.

1. Move the board card to **In Progress** (use option ID from `platform-docs/project-board.md`):
   ```bash
   # Step 1: get item ID (already available from the Todo query above)
   ITEM_ID=<id from Step 2 query>

   # Step 2: set status to In Progress
   GH_HOST=<host> gh api graphql -f query="
   mutation { updateProjectV2ItemFieldValue(input:{
     projectId: \"<PROJECT_ID>\"
     itemId: \"$ITEM_ID\"
     fieldId: \"<STATUS_FIELD_ID>\"
     value: { singleSelectOptionId: \"<IN_PROGRESS_OPTION_ID>\" }
   }) { projectV2Item { id } } }"
   ```

   Get project global ID, field ID, and option IDs from `platform-docs/project-board.md`.
   If this file is missing or out of date, query once:
   ```bash
   GH_HOST=<host> gh api graphql -f query='
   { organization(login:"<org>") { projectV2(number:<N>) {
     id
     fields(first:20) { nodes {
       ... on ProjectV2SingleSelectField { id name options { id name } }
     }}
   } } }'
   ```

3. Post a brief "starting" comment on the issue:
   ```
   Starting work on this issue. Reading acceptance criteria and planning approach.
   ```

4. Write the **issue-refs.json sidecar** in the current session directory so downstream
   tools (cost-retrospective, session-handoff) can attribute costs to this issue exactly.

   ```bash
   # Resolve the current session ID
   SESSION_ID="${COPILOT_SESSION_ID:-}"

   # Fallback: most-recent session in session_store.db matching current repo
   if [ -z "$SESSION_ID" ]; then
     REPO=<repo>   # e.g. custom-repo/custom-repo-linux
     SESSION_ID=$(python3 - <<'EOF'
   import sqlite3, os, sys
   db = os.path.expanduser("~/.copilot/session-store.db")
   repo = os.environ.get("REPO", "")
   if not os.path.exists(db):
       sys.exit(0)
   conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
   cur = conn.cursor()
   cur.execute(
       "SELECT id FROM sessions WHERE repository=? ORDER BY created_at DESC LIMIT 1",
       (repo,)
   )
   row = cur.fetchone()
   conn.close()
   print(row[0] if row else "")
   EOF
   )
   fi

   # Get the integer API ID for the issue (needed by cost-retrospective.py)
   ISSUE_API_ID=$(GH_HOST=<host> gh api repos/<repo>/issues/<N> --jq '.id')

   # Write (overwrite) the sidecar
   if [ -n "$SESSION_ID" ] && [ -d "$HOME/.copilot/session-state/$SESSION_ID" ]; then
     python3 -c "
   import json, datetime, sys
   sidecar = {
     'issue_number': <N>,
     'issue_api_id': int('$ISSUE_API_ID'),
     'confidence': 'exact',
     'activated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
   }
   path = '$HOME/.copilot/session-state/$SESSION_ID/issue-refs.json'
   with open(path, 'w') as f:
     json.dump(sidecar, f, indent=2)
   print('issue-refs.json written:', path)
   "
   else
     echo "WARNING: session directory not found for SESSION_ID='$SESSION_ID' — issue-refs.json not written"
   fi
   ```

   **Notes:**
   - Replace `<N>` with the actual issue number, `<repo>` with the repo slug, `<host>` with the GHE host.
   - The file is always **overwritten**, not appended — a session can only be active on one issue at a time.
   - A missing session directory produces a warning log only; it must never crash the skill.
   - The sidecar is read by the `session-handoff` skill and by `cost-retrospective.py` to assign `confidence="exact"` issue attribution.

5. Propose a concrete plan based on the acceptance criteria. Wait for user confirmation
   before starting implementation.

**HW-Test and Integration — special handling:**

When an issue is in **HW-Test** (human moved it there):
- SSH to the device in `platform-docs/context-<repo>.md`
- Run each acceptance test step from the issue body
- Post results as a comment on the issue
- Human moves to Integration or Done — agent does NOT move the card

When an issue is in **Integration**:
- First check that **all sibling issues** under the same epic are also in Integration. To do this,
  list all sub-issues of the epic and check their board status:
  ```bash
  # List sibling issues under epic #<EPIC_N>
  GH_HOST=<host> gh issue view <EPIC_N> --repo <repo> --json subIssues
  # Then for each, check board status
  ```
  If any sibling is NOT in Integration, post a comment on the epic: "Integration gate not met:
  #X, #Y are not yet in Integration." Do not run tests.
- When all siblings are in Integration: SSH to the device, run the combined test steps from the
  **epic body**, post results as a comment **on the epic**.
- After posting results: do NOT move any card. Human decides next action.

---

When implementation is complete:

1. Move the board card to **Review** (use the Review option ID from `platform-docs/project-board.md`).

2. Post a review comment on the issue with this structure:
   ```markdown
   ## Review — <issue title>

   ### What was done
   - <specific change 1 with file path and what changed>
   - <specific change 2>

   ### Decisions taken
   - <decision and rationale — e.g. "Used X over Y because Z">

   ### How to verify
   - [ ] <concrete test command or exact observable result>
   - [ ] <second check>

   ### Known limitations / follow-ups
   - <anything left open, or "none">
   ```

3. If a PR was opened for this issue: post a comment on the **issue** with the PR reference:
   ```
   PR opened: #<PR_NUM> <title> — <url>
   ```
   Do **NOT** add the PR to the project board — only issues belong on the board.
   Adding a PR creates a duplicate card alongside the issue.

4. Do **not** close the issue — that is a human action after review.

---

## Step 6 — If blocked (needs-human)

If you cannot proceed without a human decision:

1. Add label `needs-human`:
   ```bash
   GH_HOST=<host> gh issue edit <N> --repo <repo> --add-label needs-human
   ```

2. Post a detailed blocker comment:
   ```markdown
   ## Blocked — needs human input

   **What I need:** <exact question or decision>

   **Context:** <why this can't be resolved automatically, what I've already tried>

   **Options considered:**
   - Option A: <description> — <trade-off>
   - Option B: <description> — <trade-off>

   **To unblock:** <what the human should do, e.g. "Answer in a comment below" or "Check credentials at X">
   ```

3. Move board card to **Review** (not Todo — agents never set Todo) and post a blocker comment.

---

## Step 7 — If no Todo issues exist

Check if any `needs-human` issues might now be unblocked. If so, tell the user which ones
and ask them to remove the `needs-human` label and move the card to **Todo** (only humans
move cards to Todo). Otherwise tell the user there are no ready issues and ask what to work on.
