---
name: session-handoff
description: Closes the current work session cleanly. Extracts lessons learned, updates
             context files, ticks completed spec/plan checkboxes, and outputs a ready-to-paste
             prompt for the next session. The prompt includes a proposed session plan the user
             can confirm or adjust before work begins. Use when wrapping up before a new session.
---

# Skill: Session Handoff

Closes any work session by recording durable knowledge and generating a next-session prompt.
Run this before `/clear` or starting a new session.

---

## Step 1 — Collect session state

Answer these from the conversation history:

1. **What was the goal?** (one sentence)
2. **What was completed?** (bullet list)
3. **What is in progress or blocked?** (e.g. a build running, a deploy pending)
4. **What is next?** (immediate next action for a fresh agent)
5. **What non-obvious things did we discover?** (root causes, gotchas, wrong assumptions corrected)
6. **What files were changed?** (check git status across repo and submodules)
7. **What is the state of any connected devices or services?**
8. **What issue was active this session?** Read the `issue-refs.json` sidecar:

   ```bash
   # Resolve current session ID
   SESSION_ID="${COPILOT_SESSION_ID:-}"
   if [ -z "$SESSION_ID" ]; then
     REPO=$(git remote get-url origin 2>/dev/null | sed -E 's|.*[:/]([^/]+/[^/]+)\.git$|\1|; s|.*[:/]([^/]+/[^/]+)$|\1|')
     SESSION_ID=$(python3 - <<'EOF'
   import sqlite3, os
   db = os.path.expanduser("~/.copilot/session-store.db")
   repo = os.environ.get("REPO", "")
   if not os.path.exists(db):
       exit(0)
   conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
   cur = conn.cursor()
   cur.execute("SELECT id FROM sessions WHERE repository=? ORDER BY created_at DESC LIMIT 1", (repo,))
   row = cur.fetchone()
   conn.close()
   print(row[0] if row else "")
   EOF
     )
   fi

   SIDECAR="$HOME/.copilot/session-state/$SESSION_ID/issue-refs.json"
   if [ -f "$SIDECAR" ]; then
     cat "$SIDECAR"
   else
     echo "INFO: no issue-refs.json sidecar found at $SIDECAR — issue attribution will be unknown"
   fi
   ```

   If the sidecar is present, record its contents for inclusion in the handoff output.
   A missing sidecar is not an error — just note it clearly in the handoff and proceed.

Check session todos if any were tracked:
```sql
SELECT id, title, status FROM todos ORDER BY id;
```

---

## Step 2 — Tick completed checkboxes in spec/plan files

Only search in active plan/spec files — not in skill docs, issue templates, or examples.
Look for `- [ ]` only in files under `docs/` and any session plan files:

```bash
grep -rn '\- \[ \]' --include="*.md" docs/ ~/.copilot/session-state/ 2>/dev/null | grep -v "SKILL\|template\|example" | head -30
```

Use the edit tool to change `- [ ]` → `- [x]` for items confirmed complete in this session.
Do not tick items you have not verified completed.

---

## Step 3 — Update context file

Resolve the per-repo context file:
```bash
REPO_NAME=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' \
  | cut -d/ -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
CONTEXT_FILE="platform-docs/context-${REPO_NAME}.md"
```

File: `$CONTEXT_FILE`

> **Per-track rule:** `$CONTEXT_FILE` has one `## Track: …` section per active project track.
> **Update ONLY the section whose `Branch:` field matches your current branch.**
> Never rewrite sections belonging to other tracks — other parallel sessions own them.
>
> Your track section = `## Track: <branch-prefix> — Project #<N>` where branch matches
> `git rev-parse --abbrev-ref HEAD`.
>
> If no section exists for your branch yet, **add a new `## Track:` section** after the last
> existing track section and before `## Shared`.

Identify your track section and update these sub-sections within it:

**A. Branch / PR / Active project** — at the top of your track section

**B. `### Issue Status`** — table of open issues for your project; remove closed ones

**C. `### Needs Human`** — issues with `needs-human` label and what is needed

**D. `### Device State`** — if your track uses a device (IP, slot, version, post-boot notes)

**E. `### Next Action`** — one concise paragraph: what to do immediately, what blockers remain

Also update **`## Shared`** at the bottom for facts that span all tracks (build notes, epic overview).

**Lessons** — append to `platform-docs/knowledge/lessons-learned.md` (not to context.md):
- One bullet per non-obvious discovery
- Format: `- [YYYY-MM-DD] <concise lesson>`
- Only include things that save a future agent time

---

## Step 3b — Audit docs and issues

**Issues:**

1. **Completed issues** — move to **Review** and post a review comment. Do NOT close them —
   that is a human action after reviewing the work.

2. **Issues still In Progress** — move to **Review** (do NOT leave In Progress, do NOT move
   to Todo — agents never set Todo):
   ```bash
   # Get ITEM_ID for issue N from the board, then:
   GH_HOST=<host> gh api graphql -f query="
   mutation { updateProjectV2ItemFieldValue(input:{
     projectId:\"<PROJECT_ID>\"
     itemId:\"$ITEM_ID\"
     fieldId:\"<FIELD_ID>\"
   value:{singleSelectOptionId:\"<REVIEW_OPTION_ID>\"}
   }){ projectV2Item { id } } }"
   ```
   Use `<REVIEW_OPTION_ID>` from `platform-docs/project-board.md` for the active project.
   Post a progress comment on the issue:
   ```markdown
   ## Paused — session ending

   **Progress:** <what was done>
   **Next step:** <exact action for the next agent to continue>
   **State:** <any relevant state — e.g. build running, files partially modified>
   ```

3. **Issues in HW-Test or Integration** — leave them in place (human-managed stages).
   List them in the handoff summary so the user can decide next action.

3. **Create issues for discovered work** — use the issue-create skill. New issues go to **Backlog**.

4. **Issues with `needs-human`** — list them in the handoff summary so the user sees them.

5. **No markdown task lists** — if any were created, convert to issues now.

**Docs** — check against `docs/doc-rules.md`:
- Postmortems have the four-field header (Status / Root cause / Fix / Still at risk)
- Lessons go to `platform-docs/knowledge/lessons-learned.md`
- No stale content in context docs

---

## Step 4 — Update layer/module context files

If work touched a submodule, add 1–3 bullets under `## Recent changes` in that module's
`CLAUDE.md` or `AGENTS.md`. Only if knowledge is component-specific.

---

## Step 5 — Commit all context changes

**Never push directly to a protected branch (`kirkstone-dev`, `scarthgap-dev`, `main` in code repos).** Context/docs changes go on the current feature branch and are included in its PR. If you are not already on a feature branch, create one first.

```bash
REPO_NAME=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' \
  | cut -d/ -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
CONTEXT_FILE="platform-docs/context-${REPO_NAME}.md"

git add "$CONTEXT_FILE"
git add <any spec/plan files updated>
git commit -m "docs: session handoff — update context and progress"
# Push to the current feature branch (not directly to kirkstone-dev/main):
git push
```

If a PR does not yet exist for this branch, create one now:
```bash
gh pr create --base kirkstone-dev --title "docs: session handoff context update" --body "Auto-generated context update from session handoff."
```

---

## Step 6 — Output the next-session prompt

```bash
REPO_NAME=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' \
  | cut -d/ -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
CONTEXT_FILE="platform-docs/context-${REPO_NAME}.md"

# Read active project number from context file
BRANCH=$(git rev-parse --abbrev-ref HEAD)
PROJECT_NUM=$(awk "/Branch.*$BRANCH/,/^---/" "$CONTEXT_FILE" | grep 'Active project' | sed -E 's/.*#([0-9]+).*/\1/' | head -1)

REMOTE_URL=$(git remote get-url origin 2>/dev/null)
TOP_REPO=$(echo "$REMOTE_URL" | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|')
GHE_HOST=$(echo "$REMOTE_URL" | sed -E 's|.*@([^:]+):.*|\1|; s|https?://([^/]+)/.*|\1|')
ORG=$(echo "$TOP_REPO" | cut -d/ -f1)

echo "=== Todo issues (ready for next session) ==="
GH_HOST=$GHE_HOST gh api graphql -f query="
{ organization(login:\"$ORG\") { projectV2(number:$PROJECT_NUM) { items(first:100) {
  nodes {
    content { ... on Issue { number title state } }
    fieldValues(first:10) { nodes {
      ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { name } } }
    }}
  }
} } } }" \
  --jq '[.data.organization.projectV2.items.nodes[]
    | select(.content.state=="OPEN")
    | { number: .content.number, title: .content.title,
        stage: (.fieldValues.nodes[] | select(.field.name=="Status") | .name) }
    | select(.stage=="Todo")]
    | sort_by(.number)[] | "  #\(.number) \(.title)"'

echo ""
echo "=== Needs-human (blocked on human input) ==="
GH_HOST=$GHE_HOST gh issue list --repo "$TOP_REPO" --label needs-human --state open \
  --json number,title --jq '.[] | "  #\(.number) \(.title)"'
```

Print a self-contained prompt block:

**Part A — Context:**
```
<one-sentence goal and context>

Repo: <absolute path>
Branch: <branch>

Active issue this session:
  issue_number: <N or "unknown (no sidecar)">
  issue_api_id: <integer or null>
  confidence: <"exact" | "unknown">
  activated_at: <UTC ISO-8601 or null>

Completed this session:
- <bullet>

Pending:
- <e.g. "Board has issue #N in Todo — pick it up">

Constraints:
- <e.g. "Do not change MACHINE name">

<Device access if needed>

Open Todo issues (from board at handoff time):
<paste numbered list>

Needs-human issues (blocked on human input):
<paste list or "none">

Start: pick the lowest-numbered Todo issue, move it to In Progress on the board,
read its body and comments, then propose a plan before doing any work.
```

**Part B — Proposed plan:**
```
Proposed plan for this session:
1. <first concrete step>
2. <second step>
3. <...>

Please confirm this plan or tell me what to change before I start.
```

---

## Step 7 — Copy to clipboard

```bash
cat <<'PROMPT' > /tmp/next-session-prompt.txt
<full prompt here>
PROMPT
cat /tmp/next-session-prompt.txt | pbcopy   # macOS
# or: xclip -selection clipboard < /tmp/next-session-prompt.txt  # Linux
```

> **Prompt copied to clipboard.**
> Run `/new` to start a new conversation, then paste with ⌘V (or Ctrl+V).

---

## Checklist

- [ ] Session state collected
- [ ] Spec/plan checkboxes ticked
- [ ] `platform-docs/context-<repo-name>.md` updated — **only your track's section** (Issue Status, Needs Human, Device State, Next Action)
- [ ] New lessons appended to `platform-docs/knowledge/lessons-learned.md`
- [ ] Completed issues moved to Review with review comment (human will close and move to Done)
- [ ] In-Progress issues moved back to Todo with progress comment
- [ ] New discovered-work issues created (Backlog stage)
- [ ] needs-human issues listed in handoff
- [ ] No markdown task lists left behind
- [ ] All changes committed
- [ ] Next-session prompt printed (Part A + Part B) and copied to clipboard
