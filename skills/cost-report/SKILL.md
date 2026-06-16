---
name: cost-report
description: >
  Queries Copilot session cost data grouped by issue. Supports default weekly
  summary, per-issue session breakdown, and top-N issue ranking. Use when asked
  about Copilot usage costs, premium requests per issue, or cost attribution.
---

# Skill: Cost Report

Query Copilot session cost data from local JSONL logs using a transient SQLite database.

---

## How to invoke

The user may ask things like:
- "Show me this week's Copilot costs"
- "How many premium requests did issue #86 use?"
- "What are the top 10 most expensive issues?"
- "Show costs since 2024-06-01"
- "Cost report for repo custom-repo/custom-repo-linux"

Map their request to arguments and run:

```bash
python3 ~/.copilot/skills/cost-report/query.py [OPTIONS]
```

## Options reference

| Option | Description |
|--------|-------------|
| *(none)* | Default: this week's sessions grouped by issue |
| `--issue N` | Session breakdown for issue #N |
| `--top N` | Top N issues ranked by premium requests |
| `--since YYYY-MM-DD` | Filter sessions on or after this date |
| `--repo OWNER/REPO` | Filter to a specific repository |

Options compose: e.g. `--top 5 --since 2024-06-01 --repo custom-repo/custom-repo-linux`

---

## Step 1 — Parse the user's request

Identify:
1. **Query mode**: default week / `--issue N` / `--top N`
2. **Filters**: `--since DATE` and/or `--repo SLUG` if mentioned

If the user mentions "this week" or no time frame → default (no `--since`).
If the user mentions "issue #N" or "issue N" → `--issue N`.
If the user mentions "top N" or "top-10" → `--top N` (default N=10 if unspecified).

---

## Step 2 — Run the query

```bash
python3 ~/.copilot/skills/cost-report/query.py [FLAGS]
```

Examples:
```bash
# Weekly summary
python3 ~/.copilot/skills/cost-report/query.py

# Per-issue breakdown
python3 ~/.copilot/skills/cost-report/query.py --issue 86

# Top 10 issues
python3 ~/.copilot/skills/cost-report/query.py --top 10

# With filters
python3 ~/.copilot/skills/cost-report/query.py --top 5 --since 2024-06-01
python3 ~/.copilot/skills/cost-report/query.py --issue 88 --repo custom-repo/custom-repo-linux
```

---

## Step 3 — Present the output

The script outputs a markdown table. Display it directly in the chat.

If the output is an italic empty-state message (e.g. `_No sessions recorded this week._`),
tell the user no matching data was found and suggest:
- Running `python3 scripts/cost-retrospective.py` to backfill historical data
- Checking that `~/.copilot/cost-log.jsonl` or `~/.copilot/cost-log-historical.jsonl` exist

---

## Notes

- Both `~/.copilot/cost-log.jsonl` (live, from sessionEnd hook) and
  `~/.copilot/cost-log-historical.jsonl` (historical backfill) are merged automatically.
- Records are deduplicated by `session_id` before SQL insert; live data wins.
- The SQLite database is transient (`:memory:`) — nothing is written to disk.
- `—` in output means the value was not recorded (not zero).
- `est_cost_usd` is `—` until pricing is wired into cost-sync.py.
