---
name: pipeline-stage-handoff
description: Records lessons learned at the end of a HIL pipeline stage (HW-Check, HW-Update, HW-Stress). Extracts non-obvious discoveries from the current stage run, appends them to lessons-learned.md with stage + version context, and commits. Lightweight alternative to session-handoff — no context file rewrite, no PR audit, no next-session prompt.
---

# Skill: Pipeline Stage Handoff

Run at the end of every HIL pipeline stage, just before or just after calling `pipeline_advance`.
Captures durable knowledge from this stage run into `hil-stress/lessons-learned.md`.

---

## Step 1 — Identify stage context

Resolve from environment or working state:

```bash
STAGE="${PIPELINE_STAGE:-unknown}"          # e.g. HW-Check, HW-Update, HW-Stress
VERSION=$(cat /tmp/fw-version.txt 2>/dev/null || echo "unknown")
ISSUE_NUM="${ISSUE_NUM:-unknown}"
DATE=$(date +%Y-%m-%d)
LESSONS_FILE="hil-stress/lessons-learned.md"
```

---

## Step 2 — Extract lessons from this stage

Review the current stage's output and identify **non-obvious** discoveries only:

**Include:**
- New skip root causes (e.g. "test X skips because sysfs path Y absent on HW1")
- Unexpected failures and their root causes
- Environmental issues (VPN, SSH, device state anomalies)
- Wrong assumptions that were corrected
- Patterns that will recur and save a future agent time
- Fixes applied mid-stage that are not obvious from the code

**Exclude:**
- Expected outcomes (all tests passed, device rebooted normally)
- Things already in `lessons-learned.md` — check before appending:
  ```bash
  grep -c "pattern" "$LESSONS_FILE" 2>/dev/null
  ```
- Obvious facts (SSH requires VPN, distupgrade requires a bundle)

---

## Step 3 — Append to lessons-learned.md

For each lesson, append one bullet. Tag with stage and version for traceability:

```bash
cat >> "$LESSONS_FILE" << EOF

- [$DATE $STAGE $VERSION] <concise lesson — one sentence, actionable>
EOF
```

Format: `- [YYYY-MM-DD STAGE VERSION] lesson text`

Examples of good lessons:
```
- [2026-06-15 HW-Stress v0.2+140] Tests 8.1–8.4 skip on HW1 because TOF_I2C_PATH=/sys/bus/i2c/devices/2-0057 is HW2-specific. HW1 uses USB BTA camera — check lsusb | grep -i bta instead.
- [2026-06-15 HW-Update v0.2+140] hil-download-bundles.sh needs AZURE_STORAGE_TOKEN pre-fetched on host via az account get-access-token before container start — it cannot refresh tokens inside the container.
- [2026-06-15 HW-Check v0.2+140] device-status.json can contain stale SSH_unreachable records from a previous failed run — always verify timestamps match the current run before reporting device counts.
```

If there are **no new lessons** (the run was fully nominal with no surprises), write a single line:

```bash
echo "- [$DATE $STAGE $VERSION] Nominal run — no new lessons." >> "$LESSONS_FILE"
```

---

## Step 4 — Commit and push

```bash
cd "$(git rev-parse --show-toplevel)"
git add hil-stress/lessons-learned.md
git diff --cached --stat

# Only commit if there are staged changes
if ! git diff --cached --quiet; then
  git commit -m "lessons: $STAGE $VERSION — post-stage handoff"
  git push
  echo "✓ Lessons committed"
else
  echo "✓ No new lessons to commit"
fi
```

---

## Rules

- One bullet per distinct discovery — do not combine unrelated things
- Never log passwords, tokens, IP addresses, or device serial numbers in lessons
- Do not rewrite existing bullets — append only
- Do not update `context-custom-repo-sensei-o.md` — that is for `session-handoff` only
- Do not create issues here — use `issue-create` skill for discovered QA findings
