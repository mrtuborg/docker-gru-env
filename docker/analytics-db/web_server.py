#!/usr/bin/env python3
"""
gru-analytics-web — minimal stdlib-only HTTP dashboard for gru-analytics-db.

Runs inside the gru-analytics-db container itself (see entrypoint.sh), reads
pipeline_runs / pipeline_run_items via `psql` over the local unix socket (no
extra DB driver needed — psql already ships in the postgres:16-alpine base
image), and renders a self-contained HTML page per request (vanilla SVG
charts, no client-side framework or build step).

Its lifecycle is 1:1 with the database container: it starts when postgres
becomes ready and stops when the container stops. It is intentionally
decoupled from gru-server — the pipeline-management app — so the DB's own
analytics remain visible even if gru-server is down.

Routes:
    GET /                    overview: totals, activity heatmap, cost/items
                             by pipeline, recent runs table
    GET /run/<run_id>        single run: stats, item timeline, items table
    GET /healthz             plaintext "ok" — liveness check

Env vars (mirrors the postgres container's own):
    POSTGRES_USER        default "gru"
    POSTGRES_DB          default "gru_analytics"
    ANALYTICS_WEB_PORT   default 8080
"""
from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PG_USER = os.environ.get("POSTGRES_USER", "gru")
PG_DB = os.environ.get("POSTGRES_DB", "gru_analytics")
WEB_PORT = int(os.environ.get("ANALYTICS_WEB_PORT", "8080"))

_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_SUCCESS_STATUSES = ("success", "completed", "done")


class QueryError(Exception):
    """Raised when psql fails (DB unreachable, bad SQL, etc.)."""


def run_query(sql: str) -> list[dict]:
    """Execute a read-only SQL statement via psql --csv and parse rows as dicts.

    No query is ever built from unsanitized user input — all callers validate
    identifiers with _IDENT_RE / int() before interpolating them into SQL.
    """
    try:
        proc = subprocess.run(
            ["psql", "-U", PG_USER, "-d", PG_DB, "-v", "ON_ERROR_STOP=1", "--csv", "-c", sql],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise QueryError(str(exc)) from exc
    if proc.returncode != 0:
        raise QueryError(proc.stderr.strip() or "psql failed")
    reader = csv.DictReader(io.StringIO(proc.stdout))
    return list(reader)


def _num(v: str | None, cast=float, default=0):
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except ValueError:
        return default


def _validate_ident(value: str) -> str:
    if not _IDENT_RE.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


# ── Data access ───────────────────────────────────────────────────────────────

def fetch_overview(days: int) -> dict:
    totals = run_query(
        """SELECT COUNT(*) AS total_runs,
                  COALESCE(SUM(issues_processed),0) AS total_processed,
                  COALESCE(SUM(issues_succeeded),0) AS total_succeeded,
                  COALESCE(SUM(issues_failed),0)    AS total_failed
           FROM pipeline_runs"""
    )[0]

    item_totals = run_query(
        """SELECT COUNT(*) AS total_items,
                  COALESCE(SUM(CASE WHEN status IN ('success','completed','done') THEN 1 ELSE 0 END),0) AS items_succeeded,
                  COALESCE(SUM(cost_usd),0)      AS total_cost_usd,
                  COUNT(cost_usd)                AS items_with_cost,
                  COALESCE(SUM(tokens_input),0)  AS total_tokens_input,
                  COALESCE(SUM(tokens_output),0) AS total_tokens_output,
                  COALESCE(AVG(duration_s),0)    AS avg_duration_s
           FROM pipeline_run_items"""
    )[0]

    by_pipeline = run_query(
        """SELECT pr.pipeline_id,
                  COUNT(DISTINCT pr.id) AS runs,
                  COUNT(ri.*)           AS items,
                  COALESCE(SUM(CASE WHEN ri.status IN ('success','completed','done') THEN 1 ELSE 0 END),0) AS succeeded,
                  COALESCE(SUM(ri.cost_usd),0) AS cost_usd
           FROM pipeline_runs pr
           LEFT JOIN pipeline_run_items ri ON ri.run_id = pr.id
           GROUP BY pr.pipeline_id
           ORDER BY items DESC"""
    )

    runs = run_query(
        """SELECT id, pipeline_id, started_at::text, ended_at::text, status,
                  issues_processed, issues_succeeded, issues_failed, model_used,
                  EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at)) AS duration_s
           FROM pipeline_runs
           ORDER BY started_at DESC
           LIMIT 200"""
    )

    days = max(1, min(days, 365))  # clamp — keeps the heatmap and query window in sync
    daily = run_query(
        f"""SELECT date_trunc('day', started_at)::date::text AS day,
                   COUNT(*) AS items,
                   COALESCE(SUM(cost_usd),0) AS cost_usd
            FROM pipeline_run_items
            WHERE started_at >= now() - INTERVAL '{days} days'
            GROUP BY 1 ORDER BY 1"""
    )

    total_items = _num(item_totals["total_items"], int)
    items_succeeded = _num(item_totals["items_succeeded"], int)
    items_with_cost = _num(item_totals["items_with_cost"], int)

    now = datetime.now(timezone.utc)
    STALE_RUNNING_S = 3 * 3600  # flag "running" rows older than this as likely stuck
    runs_out = []
    for r in runs:
        duration_s = _num(r["duration_s"])
        is_stale = r["status"] == "running" and duration_s > STALE_RUNNING_S
        runs_out.append({
            "id": r["id"], "pipeline_id": r["pipeline_id"],
            "started_at": r["started_at"], "ended_at": r["ended_at"] or None,
            "status": r["status"], "issues_processed": _num(r["issues_processed"], int),
            "issues_succeeded": _num(r["issues_succeeded"], int),
            "issues_failed": _num(r["issues_failed"], int),
            "model_used": r["model_used"] or None,
            "duration_s": round(duration_s, 1),
            "is_stale": is_stale,
        })

    return {
        "days": days,
        "summary": {
            "total_runs": _num(totals["total_runs"], int),
            "total_processed": _num(totals["total_processed"], int),
            "total_succeeded": _num(totals["total_succeeded"], int),
            "total_failed": _num(totals["total_failed"], int),
            "total_items": total_items,
            "items_succeeded": items_succeeded,
            "items_failed": total_items - items_succeeded,
            "success_rate": round(items_succeeded / total_items * 100, 1) if total_items else 0.0,
            "total_cost_usd": round(_num(item_totals["total_cost_usd"]), 4),
            "items_with_cost": items_with_cost,  # 0 ⇒ cost figures are "no data", not "$0"
            "total_tokens_input": _num(item_totals["total_tokens_input"], int),
            "total_tokens_output": _num(item_totals["total_tokens_output"], int),
            "avg_duration_s": round(_num(item_totals["avg_duration_s"]), 1),
        },
        "by_pipeline": [
            {
                "pipeline_id": r["pipeline_id"],
                "runs": _num(r["runs"], int),
                "items": _num(r["items"], int),
                "succeeded": _num(r["succeeded"], int),
                "cost_usd": round(_num(r["cost_usd"]), 4),
            }
            for r in by_pipeline
        ],
        "runs": runs_out,
        "daily": [
            {"day": r["day"], "items": _num(r["items"], int), "cost_usd": round(_num(r["cost_usd"]), 4)}
            for r in daily
        ],
    }


def fetch_run_detail(run_id: str) -> dict | None:
    _validate_ident(run_id)
    escaped = run_id.replace("'", "''")
    run_rows = run_query(
        f"""SELECT id, pipeline_id, started_at::text, ended_at::text, status,
                   issues_processed, issues_succeeded, issues_failed, issues_skipped, model_used,
                   EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at)) AS duration_s
            FROM pipeline_runs WHERE id = '{escaped}'"""
    )
    if not run_rows:
        return None
    run = run_rows[0]

    items = run_query(
        f"""SELECT issue_number, issue_repo, issue_title, stage, status,
                   started_at::text, ended_at::text, duration_s, model, cost_usd,
                   session_id, error_message,
                   tokens_input, tokens_output, tokens_cache_read, tokens_reasoning,
                   nano_aiu, premium_requests, api_requests,
                   lines_added, lines_removed
            FROM pipeline_run_items
            WHERE run_id = '{escaped}'
            ORDER BY started_at ASC NULLS LAST"""
    )

    items_out = []
    for it in items:
        items_out.append({
            "issue_number": _num(it["issue_number"], int),
            "issue_repo": it["issue_repo"],
            "issue_title": it["issue_title"] or None,
            "stage": it["stage"], "status": it["status"],
            "started_at": it["started_at"] or None, "ended_at": it["ended_at"] or None,
            "duration_s": _num(it["duration_s"]) if it["duration_s"] not in (None, "") else None,
            "model": it["model"] or None,
            "cost_usd": _num(it["cost_usd"]) if it["cost_usd"] not in (None, "") else None,
            "session_id": it["session_id"] or None,
            "error_message": it["error_message"] or None,
            "tokens_input": _num(it["tokens_input"], int) if it["tokens_input"] not in (None, "") else None,
            "tokens_output": _num(it["tokens_output"], int) if it["tokens_output"] not in (None, "") else None,
        })

    return {
        "run": {
            "id": run["id"], "pipeline_id": run["pipeline_id"],
            "started_at": run["started_at"], "ended_at": run["ended_at"] or None,
            "status": run["status"],
            "issues_processed": _num(run["issues_processed"], int),
            "issues_succeeded": _num(run["issues_succeeded"], int),
            "issues_failed": _num(run["issues_failed"], int),
            "issues_skipped": _num(run["issues_skipped"], int),
            "model_used": run["model_used"] or None,
            "duration_s": round(_num(run["duration_s"]), 1),
        },
        "items": items_out,
    }


# ── HTML rendering ────────────────────────────────────────────────────────────

BASE_CSS = """
:root {
  --bg:#0a0e14; --surface:#111820; --surface2:#161d27; --border:#1f2d3d;
  --text:#cdd9e5; --muted:#636e7b; --accent:#58a6ff; --accent2:#79c0ff;
  --green:#3fb950; --yellow:#d29922; --red:#f85149;
  --hm-0:#1e2530; --hm-1:#0e4429; --hm-2:#26a641; --hm-3:#39d353;
}
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  font-size:14px; padding:24px; max-width:1400px; margin:0 auto; }
a { color:var(--accent); text-decoration:none; }
h1 { font-size:22px; font-weight:700; color:var(--accent); text-shadow:0 0 20px rgba(88,166,255,0.4); letter-spacing:-0.3px; margin-bottom:6px; }
.meta { color:var(--muted); margin-bottom:24px; font-size:12px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:16px; margin-bottom:32px; }
.stat { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px 20px; position:relative; overflow:hidden; }
.stat::before { content:""; position:absolute; top:0; left:0; right:0; height:2px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent); opacity:0.6; }
.stat-value { font-size:28px; font-weight:700; color:var(--accent); font-variant-numeric:tabular-nums; }
.stat-label { font-size:11px; color:var(--muted); margin-top:4px; text-transform:uppercase; letter-spacing:0.08em; }
.stat-sub { font-size:10px; color:var(--muted); margin-top:2px; }
section { margin-bottom:32px; }
h2 { font-size:13px; font-weight:600; color:var(--muted); margin-bottom:12px; padding-bottom:8px;
  border-bottom:1px solid var(--border); text-transform:uppercase; letter-spacing:0.1em; }
.panel { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px; overflow-x:auto; }
table { width:100%; border-collapse:collapse; background:var(--surface); border:1px solid var(--border); border-radius:8px; overflow:hidden; }
th, td { padding:9px 12px; text-align:left; border-bottom:1px solid var(--border); font-size:12px; }
th { font-size:10px; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); background:var(--bg); font-weight:500; }
tr:last-child td { border-bottom:none; }
tr.clickable { cursor:pointer; }
tr.clickable:hover td { background:rgba(88,166,255,0.06); }
.num { text-align:right; font-variant-numeric:tabular-nums; font-family:ui-monospace,monospace; }
.mono { font-family:ui-monospace,monospace; }
.badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:0.04em; }
.badge-completed, .badge-success { background:rgba(63,185,80,0.15); color:var(--green); }
.badge-failed, .badge-failure { background:rgba(248,81,73,0.15); color:var(--red); }
.badge-running { background:rgba(88,166,255,0.15); color:var(--accent); }
.badge-unknown { background:rgba(99,110,123,0.15); color:var(--muted); }
.warn-icon { color:var(--yellow); margin-left:4px; cursor:help; }
.heatmap-wrap { display:flex; gap:3px; overflow-x:auto; padding:4px 0; }
.heatmap-week { display:flex; flex-direction:column; gap:3px; }
.heatmap-cell { width:11px; height:11px; border-radius:2px; }
.heatmap-legend { display:flex; align-items:center; gap:4px; margin-top:10px; font-size:11px; color:var(--muted); }
.heatmap-legend span.sw { display:inline-block; width:11px; height:11px; border-radius:2px; }
.back-link { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:12px; margin-bottom:16px; }
.back-link:hover { color:var(--accent); }
.error-box { background:color-mix(in srgb, var(--red) 15%, var(--bg)); border:1px solid var(--red);
  border-radius:6px; padding:16px 20px; color:var(--red); font-size:13px; }
.empty { text-align:center; padding:48px 24px; color:var(--muted); }
.combo-chart svg { display:block; width:100%; }
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{BASE_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def _stat(value: str, label: str, sub: str = "") -> str:
    sub_html = f'<div class="stat-sub">{html.escape(sub)}</div>' if sub else ""
    return f'<div class="stat"><div class="stat-value">{html.escape(value)}</div><div class="stat-label">{html.escape(label)}</div>{sub_html}</div>'


def _badge(status: str) -> str:
    cls = {"completed": "completed", "success": "success", "failed": "failed", "failure": "failure", "running": "running"}.get(status, "unknown")
    return f'<span class="badge badge-{cls}">{html.escape(status)}</span>'


def _fmt_tokens(v: int) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return str(v)


def render_overview(data: dict) -> str:
    s = data["summary"]
    cost_display = f"${s['total_cost_usd']:.2f}" if s["items_with_cost"] else "no data"
    heatmap_data = json.dumps(data["daily"])

    rows_html = []
    for r in data["by_pipeline"]:
        rows_html.append(
            f'<tr><td>{html.escape(r["pipeline_id"])}</td>'
            f'<td class="num">{r["runs"]}</td>'
            f'<td class="num">{r["items"]}</td>'
            f'<td class="num">{r["succeeded"]}</td>'
            f'<td class="num">${r["cost_usd"]:.4f}</td></tr>'
        )
    pipeline_table = "\n".join(rows_html) or '<tr><td colspan="5" class="empty">No pipelines recorded yet.</td></tr>'

    run_rows = []
    for r in data["runs"]:
        stale = ' <span class="warn-icon" title="Running longer than 3h — may be stuck">⚠</span>' if r["is_stale"] else ""
        started = html.escape(r["started_at"] or "—")
        dur = f'{r["duration_s"]:.0f}s' if r["duration_s"] else "—"
        run_rows.append(
            f'<tr class="clickable" onclick="location.href=\'/run/{html.escape(r["id"])}\'">'
            f'<td class="mono">{html.escape(r["id"])}</td>'
            f'<td>{html.escape(r["pipeline_id"])}</td>'
            f'<td>{started}</td>'
            f'<td class="num">{dur}</td>'
            f'<td class="num">✓{r["issues_succeeded"]} ✕{r["issues_failed"]}</td>'
            f'<td>{_badge(r["status"])}{stale}</td></tr>'
        )
    runs_table = "\n".join(run_rows) or '<tr><td colspan="6" class="empty">No pipeline runs recorded yet.</td></tr>'

    body = f"""
<h1>⬡ Gru Analytics — Database Overview</h1>
<p class="meta">Live from gru-analytics-db &nbsp;·&nbsp; generated {html.escape(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}</p>

<div class="stats">
  {_stat(str(s['total_runs']), 'Pipeline Runs')}
  {_stat(str(s['total_items']), 'Items (Sessions)', f"{s['items_succeeded']} succeeded · {s['items_failed']} failed")}
  {_stat(f"{s['success_rate']}%", 'Success Rate')}
  {_stat(cost_display, 'Est. Cost (USD)', '' if s['items_with_cost'] else f"cost_usd not populated for {s['total_items']} item(s)")}
  {_stat(_fmt_tokens(s['total_tokens_input'] + s['total_tokens_output']), 'Tokens (in+out)')}
</div>

<section>
  <h2>Activity — Last {data['days']} Days</h2>
  <div class="panel">
    <div id="heatmap" class="heatmap-wrap"></div>
    <div class="heatmap-legend">
      Less &nbsp;<span class="sw" style="background:var(--hm-0)"></span>
      <span class="sw" style="background:var(--hm-1)"></span>
      <span class="sw" style="background:var(--hm-2)"></span>
      <span class="sw" style="background:var(--hm-3)"></span>&nbsp; More
    </div>
  </div>
</section>

<section>
  <h2>Items &amp; Cost by Pipeline</h2>
  <table>
    <thead><tr><th>Pipeline</th><th>Runs</th><th>Items</th><th>Succeeded</th><th>Cost (USD)</th></tr></thead>
    <tbody>{pipeline_table}</tbody>
  </table>
</section>

<section>
  <h2>Recent Pipeline Runs</h2>
  <table>
    <thead><tr><th>Run</th><th>Pipeline</th><th>Started</th><th>Duration</th><th>Issues</th><th>Status</th></tr></thead>
    <tbody>{runs_table}</tbody>
  </table>
</section>

<script>
const DAYS = {data['days']};
const DAILY = {heatmap_data};
(function renderHeatmap() {{
  const byDay = new Map(DAILY.map(d => [d.day, d.items]));
  const today = new Date();
  const days = [];
  for (let i = DAYS - 1; i >= 0; i--) {{
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    const key = d.toISOString().slice(0, 10);
    days.push({{ date: key, items: byDay.get(key) || 0 }});
  }}
  const max = Math.max(...days.map(d => d.items), 1);
  const level = v => v === 0 ? 0 : v / max > 0.66 ? 3 : v / max > 0.33 ? 2 : 1;
  const colors = ['var(--hm-0)', 'var(--hm-1)', 'var(--hm-2)', 'var(--hm-3)'];
  const weeks = [];
  let cur = [];
  days.forEach((d, i) => {{
    cur.push(d);
    if (cur.length === 7 || i === days.length - 1) {{ weeks.push(cur); cur = []; }}
  }});
  const wrap = document.getElementById('heatmap');
  wrap.innerHTML = weeks.map(week =>
    '<div class="heatmap-week">' +
    week.map(d => `<div class="heatmap-cell" style="background:${{colors[level(d.items)]}}" title="${{d.date}}: ${{d.items}} item(s)"></div>`).join('') +
    '</div>'
  ).join('');
}})();
</script>
"""
    return _page("Gru Analytics — Overview", body)


def render_run_detail(data: dict) -> str:
    run = data["run"]
    items = data["items"]
    total_cost = sum(i["cost_usd"] for i in items if i["cost_usd"] is not None)
    items_with_cost = sum(1 for i in items if i["cost_usd"] is not None)
    total_tokens = sum((i["tokens_input"] or 0) + (i["tokens_output"] or 0) for i in items)

    item_rows = []
    for it in items:
        cost = f"${it['cost_usd']:.4f}" if it["cost_usd"] is not None else "—"
        dur = f'{it["duration_s"]:.0f}s' if it["duration_s"] is not None else "—"
        tokens = f'{it["tokens_input"] or 0} / {it["tokens_output"] or 0}' if (it["tokens_input"] is not None or it["tokens_output"] is not None) else "—"
        title_line = f'<div style="color:var(--muted);font-size:11px">{html.escape(it["issue_title"])}</div>' if it["issue_title"] else ""
        error_line = f'<div style="color:var(--red);font-size:11px;margin-top:2px">{html.escape(it["error_message"])}</div>' if it["error_message"] else ""
        item_rows.append(
            f'<tr><td class="mono">{html.escape(it["issue_repo"])}#{it["issue_number"]}{title_line}</td>'
            f'<td>{html.escape(it["stage"])}</td>'
            f'<td>{_badge(it["status"])}{error_line}</td>'
            f'<td class="num">{dur}</td>'
            f'<td class="num">{cost}</td>'
            f'<td style="font-size:11px;color:var(--muted)">{html.escape(it["model"] or "—")}</td>'
            f'<td class="num" style="font-size:11px;color:var(--muted)">{tokens}</td></tr>'
        )
    items_table = "\n".join(item_rows) or '<tr><td colspan="7" class="empty">No items recorded for this run.</td></tr>'

    cost_display = f"${total_cost:.4f}" if items_with_cost else "no data"

    body = f"""
<a class="back-link" href="/">&larr; Back to overview</a>
<h1 class="mono" style="font-size:18px">{html.escape(run['id'])}</h1>
<p class="meta">{html.escape(run['pipeline_id'])} &nbsp;·&nbsp; {html.escape(run['started_at'] or '—')} &nbsp; {_badge(run['status'])}</p>

<div class="stats">
  {_stat(str(run['issues_processed']), 'Issues Processed')}
  {_stat(str(run['issues_succeeded']), 'Succeeded')}
  {_stat(str(run['issues_failed']), 'Failed')}
  {_stat(f"{run['duration_s']:.0f}s", 'Duration')}
  {_stat(cost_display, 'Est. Cost')}
  {_stat(_fmt_tokens(total_tokens), 'Tokens')}
</div>

<section>
  <h2>Run Items ({len(items)})</h2>
  <table>
    <thead><tr><th>Issue</th><th>Stage</th><th>Status</th><th>Duration</th><th>Cost</th><th>Model</th><th>Tokens In/Out</th></tr></thead>
    <tbody>{items_table}</tbody>
  </table>
</section>
"""
    return _page(f"Run {run['id']} — Gru Analytics", body)


def render_error(status: int, message: str) -> str:
    return _page(f"Error {status}", f'<div class="error-box">{html.escape(message)}</div>')


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "gru-analytics-web/1.0"

    def log_message(self, fmt, *args):  # quieter default access log
        print(f"[gru-analytics-web] {self.address_string()} - {fmt % args}")

    def _send_html(self, status: int, body: str):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/healthz":
                self._send_html(200, "ok")
                return

            if path == "/":
                days = _num((qs.get("days") or [None])[0], int, 90)
                data = fetch_overview(days)
                self._send_html(200, render_overview(data))
                return

            m = re.match(r"^/run/([^/]+)$", path)
            if m:
                run_id = m.group(1)
                try:
                    data = fetch_run_detail(run_id)
                except ValueError as exc:
                    self._send_html(400, render_error(400, str(exc)))
                    return
                if data is None:
                    self._send_html(404, render_error(404, f"Run {run_id!r} not found"))
                    return
                self._send_html(200, render_run_detail(data))
                return

            self._send_html(404, render_error(404, "Not found"))
        except QueryError as exc:
            self._send_html(503, render_error(503, f"Analytics database unavailable: {exc}"))
        except Exception as exc:  # last-resort guard — never let the server crash on a bad request
            self._send_html(500, render_error(500, f"Internal error: {exc}"))


def main():
    server = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    print(f"[gru-analytics-web] listening on :{WEB_PORT} (db={PG_DB} user={PG_USER})")
    server.serve_forever()


if __name__ == "__main__":
    main()
