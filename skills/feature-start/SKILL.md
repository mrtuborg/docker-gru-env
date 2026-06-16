---
name: feature-start
description: Starts a new feature from `scarthgap-dev`. Analyzes the problem, decomposes
             it into GitHub issues, creates a feature branch, opens a draft PR back to
             `scarthgap-dev`, and links the issues. Use when beginning any non-trivial feature
             or refactor.
---

# Skill: Feature Start

Bootstraps a new feature: branch + draft PR + decomposed GitHub issues, all from a single
description of the problem to solve.

---

## Step 1 — Gather input

If the user hasn't described the feature:
> What feature or problem should this branch solve?

---

## Step 2 — Detect repo context

```bash
git rev-parse --abbrev-ref HEAD   # will be the PR base
git remote get-url origin
```

Parse `GH_HOST`, `REPO`, `ORG` from the remote URL. Save `BASE_BRANCH`.

**Branch convention:**
- All feature branches must start from `scarthgap-dev`.
- If current branch is NOT `scarthgap-dev`, warn and ask to confirm before branching.

---

## Step 3 — Analyse the codebase

Examine the repo to identify:
1. Affected components (layers, recipes, scripts, docs)
2. Related open issues and PRs:
   ```bash
   GH_HOST=<host> gh issue list --repo <repo> --state open \
     --json number,title --jq '.[] | "#\(.number) \(.title)"'

   GH_HOST=<host> gh pr list --repo <repo> --state open \
     --json number,title,headRefName \
     --jq '.[] | "#\(.number) [\(.headRefName)] \(.title)"'
   ```
3. Review `platform-docs/knowledge/lessons-learned.md` for relevant gotchas.

Summarise in 3–5 bullets. Ask:
> Does this analysis look right? Any corrections before I decompose into issues?

Wait for confirmation.

---

## Step 4 — Decompose into issues

Break the feature into independently-completable issues. Each must:
- Be completable in one session
- Have a clear, verifiable acceptance criterion
- Depend on at most one other issue

For each issue, use the 4-section template:

```markdown
## Context
<Why this issue exists; what problem it solves; which component it touches>

## Acceptance criteria
- [ ] <specific, verifiable outcome>

## Key files
- `path/to/file` — <why relevant>

## Blocked by
#<N> or "nothing"
```

Present the full issue list before creating anything:
> Here are the issues I plan to create. Review and confirm (or ask me to add/remove/change):

Wait for confirmation.

---

## Step 5 — Create the feature branch

```bash
FEATURE_BRANCH="feat/<short-kebab-description>"
git checkout -b "$FEATURE_BRANCH"
git push -u origin "$FEATURE_BRANCH"
```

---

## Step 6 — Open a draft PR

Create an empty bootstrap commit first (GHE rejects PRs with no commits):
```bash
git commit --allow-empty -m "chore: start $FEATURE_BRANCH"
git push
```

```bash
GH_HOST=<host> gh pr create \
  --repo <repo> \
  --base "$BASE_BRANCH" \
  --head "$FEATURE_BRANCH" \
  --draft \
  --title "feat: <feature title>" \
  --body "$(cat <<'PR'
## Context
<one paragraph: feature and why it's needed>

## Issues
<!-- filled in after issue creation -->

## Checklist
- [ ] All linked issues in Review or Done
- [ ] Device-verified
- [ ] No regressions in companion builds
PR
)"
```

---

## Step 7 — Create the GitHub issues

Create each issue in order (earlier issues get lower numbers):

```bash
GH_HOST=<host> gh issue create \
  --repo <repo> \
  --title "<title>" \
  --body "<4-section body>"
```

No labels are needed — workflow is tracked via board stages.

After creating all issues:

1. **Add every issue to the project board** and set stage:
   - Issues with `Blocked by: nothing` → **Backlog** (human promotes to Todo when ready)
   - Issues blocked by others → **Backlog** as well
   ```bash
   GH_HOST=<host> gh project item-add <PROJECT_NUM> --owner <org> \
     --url "https://<host>/<repo>/issues/<N>"
   # Then set stage to Backlog via GraphQL updateProjectV2ItemFieldValue
   ```
   Use board IDs from `platform-docs/project-board.md`.

2. Update the PR body with the issue list:
   ```bash
   GH_HOST=<host> gh pr edit <pr-number> --repo <repo> --body "<updated body>"
   ```

---

## Step 8 — Update context file

Resolve the per-repo context file and update `## Active Work`:

```bash
REPO_NAME=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*[:/]([^/]+/[^/]+)(\.git)?$|\1|' \
  | cut -d/ -f2 | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
CONTEXT_FILE="platform-docs/context-${REPO_NAME}.md"
```

Update the `## Active Work` section in `$CONTEXT_FILE`:

```markdown
## Active Work

**Branch**: `<feature-branch>`
**Base branch**: `<base-branch>`
**PR**: #<number> (draft) → `<base-branch>`
**Active project**: #<N> — <project title>
```

Also update `## Issue Status` with the newly created issues (all in Backlog stage).

Commit:
```bash
git add "$CONTEXT_FILE"
git commit -m "chore: start feature — <short title>"
git push
```

---

## Step 9 — Output summary

```
✅ Feature branch:  <branch-name>
✅ Draft PR:        #<number> → <base-branch>
✅ Issues created:  #N <title>  [Backlog]
                    #N <title>  [Backlog]
✅ Context:         platform-docs/context-${REPO_NAME}.md updated

Next step: review the issues on the project board and promote to Todo
the ones you want agents to pick up. Then start a new session.
```

---

## Rules

- **Never start coding** — this skill sets up structure only. Implementation is a separate session.
- **Always wait** for user confirmation after Step 3 (analysis) and Step 4 (issue list).
- **One PR per feature branch** — no multiple PRs.
- **All new issues go to Backlog** — the human decides when to promote them to Todo.
- **Draft PR always** — never open a ready-for-review PR at this stage.
- **No labels on issues** — board stage is the only workflow signal.
