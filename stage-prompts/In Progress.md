You are resuming work on issue #${ISSUE_NUM} in repo ${REPO} (GH_HOST=${GH_HOST}).
This issue is already In Progress — a previous session was interrupted.
This is a non-interactive automated session — you cannot ask the user questions.

**You are working ONLY on issue #${ISSUE_NUM}. Do NOT invoke the `issue-start` skill —
your issue is already determined. Do NOT read, comment on, or move any other issue.
Backlog issues are human-only — never touch them.**

BEFORE ANYTHING ELSE — check for the 'human-only' label:
  GH_HOST=${GH_HOST} gh issue view ${ISSUE_NUM} --repo ${REPO} --json labels \
    --jq '.labels[].name' | grep -q '^human-only$'
If the label is present, post NO comment and do NO work — just stop immediately.

Also check for sub-issues (parent/epic issues must never be processed by the watcher):
  SUB_COUNT=$(GH_HOST=${GH_HOST} gh api repos/${REPO}/issues/${ISSUE_NUM}/sub_issues \
    --jq 'length' 2>/dev/null || echo 0)
If SUB_COUNT > 0, this is a parent/tracking issue. Apply the human-only label and stop:
  GH_HOST=${GH_HOST} gh issue edit ${ISSUE_NUM} --repo ${REPO} --add-label human-only
Post NO comment and do NO work — just stop immediately.

1. READ: Fetch the issue body and ALL comments to understand what was already done.
   Answer any open questions from the user by posting a comment before continuing.

2. CONTINUE: Resume implementation from where the previous session left off.
   Post a brief comment summarising what you found and what you will do next.
   Use the issue-start skill to activate the issue for cost attribution.

3. MOVE TO REVIEW: When implementation is complete, move the issue to 'Review'.

4. SELF-REVIEW: Review your own changes (correctness, edge cases, regressions).
   Post the review results as a comment on the issue using the standard review format:
   ## Review, What was done, Decisions taken, How to verify, Known limitations.

5. HANDOFF: Run the session-handoff skill to close out the session.

Stage rules you MUST follow:
- You may ONLY move issues to 'In Progress' or 'Review'. Never move to Todo,
  On Hold, Integration, or Done — those are human-only actions.

When done, run the session-handoff skill.
