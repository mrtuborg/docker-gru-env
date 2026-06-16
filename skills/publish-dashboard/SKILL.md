---
name: publish-dashboard
description: >
  Regenerates all HTML cost dashboards and publishes them to GitHub Pages
  (gh-pages branch). Use when asked to publish, update, or deploy the cost
  dashboard, or to push a new report to GitHub Pages.
---

# Skill: Publish Dashboard

Regenerate all project cost dashboards and push them to the `gh-pages` branch
so they are served via GitHub Pages.

---

## How to invoke

The user may ask things like:
- "Publish the dashboard"
- "Update the cost dashboard on GitHub Pages"
- "Regenerate and deploy the dashboards"
- "Push the latest report to Pages"

---

## Step 1 — Locate the repo

The canonical repo is `~/ws/copilot-workflow`. Config lives in `.copilot-workflow/config.yml`.

```bash
SKILL_REPO=~/ws/copilot-workflow
CONFIG="$SKILL_REPO/.copilot-workflow/config.yml"
```

---

## Step 2 — Regenerate dashboards and publish

```bash
cd "$SKILL_REPO" && ./scripts/publish-ghpages.sh --config "$CONFIG" --regen
```

This does both steps in one command:
1. Runs `cost-report.py --format html --all-projects` to rebuild all HTML dashboards into `docs/`
2. Syncs `docs/` to the Pages branch and pushes

If the user only wants to regenerate without publishing:
```bash
cd "$SKILL_REPO" && GH_HOST=github.com python3 src/cost-report.py \
  --gh-host github.com \
  --format html \
  --all-projects
```

If the user only wants to publish what's already in `docs/` (no regen):
```bash
cd "$SKILL_REPO" && ./scripts/publish-ghpages.sh --config "$CONFIG"
```

---

## Step 3 — Report outcome

After the push completes, tell the user:

```
✅ Dashboard published to GitHub Pages
   URL: https://<owner>-<owner>-custom-repo-ghe-com.pages.github.com

   If this is the first publish, enable Pages in repo settings:
   Settings → Pages → Source: Deploy from branch → main / (root)
```

If the script prints "No changes to publish" — tell the user the dashboards were
already up to date and no push was needed.

---

## Error handling

| Error | Fix |
|-------|-----|
| `docs/index.html not found` | Run with `--regen` to generate dashboards first |
| `gh-pages push rejected` | Check `git remote -v` and that `GH_HOST` is correct |
| `cost-report.py` fails | Check `~/.copilot/cost-log.jsonl` exists; run `python3 src/cost-retrospective.py` to backfill |
| Pages URL returns 404 | Enable Pages in repo Settings → Pages → gh-pages branch |
