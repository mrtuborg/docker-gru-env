#!/usr/bin/env python3
"""
gru-analytics-web -- minimal stdlib-only HTTP dashboard for gru-analytics-db.

Runs inside the gru-analytics-db container itself (see entrypoint.sh), reads
projects / pipeline_runs / pipeline_run_items via `psql` over the local unix
socket (no extra DB driver needed -- psql already ships in the postgres:16-alpine
base image), and renders a self-contained HTML page per request.

The page design (CSS + chart-drawing JS) is intentionally a byte-for-byte
port of the reference "Copilot Cost" dashboard so the DB-native UI looks and
behaves identically -- see INDEX_CSS/INDEX_JS/PROJECT_CSS/PROJECT_JS below.
Only the server-side data plumbing (SQL queries + HTML templating) is new.

Its lifecycle is 1:1 with the database container: it starts when postgres
becomes ready and stops when the container stops. It is intentionally
decoupled from gru-server -- the pipeline-management app -- so the DB's own
analytics remain visible even if gru-server is down.

Data model:
    "session"  == one pipeline_run_items row (joined to its pipeline_runs
                  parent for started_at/project linkage).
    "project"  == a projects table row. Every pipeline_runs row optionally
                  links to a project via project_id; runs (and therefore
                  their sessions) with no project_id, or whose project has
                  is_unlinked=TRUE, are grouped under the synthetic
                  "Unlinked Sessions" project so every session always has a
                  project to render under, matching the reference's
                  project-unlinked.html treatment.

Routes:
    GET /                    Projects Overview (index)
    GET /project/<id>        Cost Dashboard for a single project (numeric id
                              or "unlinked")
    GET /healthz             plaintext "ok" -- liveness check

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
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PG_USER = os.environ.get("POSTGRES_USER", "gru")
PG_DB = os.environ.get("POSTGRES_DB", "gru_analytics")
WEB_PORT = int(os.environ.get("ANALYTICS_WEB_PORT", "8080"))

_IDENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
MODEL_COLORS = {"claude": "#58a6ff", "gpt": "#3fb950", "gemini": "#d29922"}


class QueryError(Exception):
    """Raised when psql fails (DB unreachable, bad SQL, etc.)."""


def run_query(sql: str) -> list[dict]:
    """Execute a read-only SQL statement via psql --csv and parse rows as dicts.

    No query is ever built from unsanitized user input -- all callers validate
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


def _num(v, cast=float, default=0):
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except ValueError:
        return default


def _opt(v):
    """None for NULL/empty CSV cells, otherwise the raw string."""
    return v if v not in (None, "") else None


def _validate_ident(value: str) -> str:
    if not _IDENT_RE.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def model_color(model_id: str | None) -> str:
    lo = (model_id or "").lower()
    for pfx, col in MODEL_COLORS.items():
        if lo.startswith(pfx):
            return col
    return "#8b949e"


def model_display_name(model_id: str | None) -> str:
    """Best-effort human label, e.g. 'claude-sonnet-4.6' -> 'Sonnet 4.6'."""
    if not model_id:
        return "Unknown"
    m = re.match(r"^(claude|gpt|gemini)-?([a-zA-Z]*)-?([\d.]*)", model_id, re.I)
    if not m:
        return model_id
    family, variant, ver = m.group(1).lower(), m.group(2), m.group(3)
    label_map = {"claude": "", "gpt": "GPT-", "gemini": "Gemini "}
    prefix = label_map.get(family, "")
    variant_part = variant.capitalize() if family == "claude" else variant.upper()
    parts = [p for p in (variant_part, ver) if p]
    return (prefix + " ".join(parts)).strip() or model_id


def fmt_tokens(v) -> str:
    v = v or 0
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return str(int(v))

INDEX_CSS = r"""
  :root {
    --bg: #0a0e14; --surface: #111820; --surface2: #161d27;
    --border: #1f2d3d; --text: #cdd9e5; --muted: #636e7b;
    --accent: #58a6ff; --accent2: #79c0ff; --green: #3fb950;
    --yellow: #d29922; --red: #f85149; --unknown: #636e7b;
  }
  [data-theme="light"] {
    --bg: #f6f8fa; --surface: #ffffff; --surface2: #f0f2f4;
    --border: #d0d7de; --text: #1f2328; --muted: #656d76;
    --accent: #0969da; --accent2: #218bff; --green: #1a7f37;
    --yellow: #9a6700; --red: #d1242f; --unknown: #656d76;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; padding: 24px; max-width: 1400px; margin: 0 auto; }

  .header { display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }
  h1 { font-size: 22px; font-weight: 700; color: var(--accent);
    text-shadow: 0 0 20px rgba(88,166,255,0.4); letter-spacing: -0.3px; }
  .meta { color: var(--muted); margin-bottom: 24px; font-size: 12px; }
  .theme-btn { background: none; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); cursor: pointer; font-size: 16px; padding: 4px 10px;
    transition: border-color 0.2s, color 0.2s; }
  .theme-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* Summary stats */
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px; margin-bottom: 32px; }
  .stat { background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; position: relative; overflow: hidden; }
  .stat::before { content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6; }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent);
    font-variant-numeric: tabular-nums; }
  .stat-label { font-size: 11px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.08em; }

  /* Project cards */
  .proj-grid { display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 16px; margin-bottom: 32px; }
  .proj-card { background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 20px; text-decoration: none; color: var(--text);
    transition: border-color 0.2s, box-shadow 0.2s; display: block; }
  .proj-card:hover { border-color: var(--accent);
    box-shadow: 0 0 20px rgba(88,166,255,0.12); }
  .proj-card-unlinked { border-color: rgba(210,153,34,0.35); }
  .proj-card-unlinked:hover { border-color: var(--yellow);
    box-shadow: 0 0 20px rgba(210,153,34,0.12); }
  .proj-card-unlinked .proj-num { color: var(--yellow); }
  .proj-card-unlinked .proj-title { color: var(--yellow); opacity: 0.85; }
  .proj-num { font-size: 11px; color: var(--muted); font-family: ui-monospace,monospace;
    margin-bottom: 4px; }
  .proj-title { font-size: 15px; font-weight: 600; color: var(--accent2);
    margin-bottom: 16px; line-height: 1.3; }
  .proj-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .proj-stat { display: flex; flex-direction: column; gap: 2px; }
  .proj-val { font-size: 18px; font-weight: 600; color: var(--text);
    font-variant-numeric: tabular-nums; }
  .proj-key { font-size: 10px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 0.07em; }

  /* Comparison chart */
  section { margin-bottom: 32px; }
  h2 { font-size: 13px; font-weight: 600; color: var(--muted); margin-bottom: 12px;
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.1em; }
  .chart-panel { background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; }
  .combo-chart { width: 100%; min-height: 200px; }
  .combo-chart svg { display: block; width: 100%; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }
  @media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }
  .chart-panel h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 12px; }
  .pie-chart-wrap { width: 100%; min-height: 200px; }
  .pie-chart-wrap svg { display: block; width: 100%; }
"""

INDEX_JS = r"""
<script>
  // ── Theme ────────────────────────────────────────────────────────────────
  (function initTheme() {
    const stored = localStorage.getItem('copilot-cost-theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = stored || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '🌙';
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
      if (!localStorage.getItem('copilot-cost-theme')) {
        const t = e.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', t);
        if (btn) btn.textContent = t === 'dark' ? '☀' : '🌙';
        if (typeof redrawAll === 'function') redrawAll();
      }
    });
    if (btn) btn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('copilot-cost-theme', next);
      btn.textContent = next === 'dark' ? '☀' : '🌙';
      if (typeof redrawAll === 'function') redrawAll();
    });
  })();

  function getColors() {
    const s = getComputedStyle(document.documentElement);
    const g = n => s.getPropertyValue(n).trim();
    return {
      bg: g('--bg'), surface: g('--surface'), surface2: g('--surface2'),
      border: g('--border'), text: g('--text'), muted: g('--muted'),
      accent: g('--accent'), accent2: g('--accent2'), green: g('--green'),
      yellow: g('--yellow'), red: g('--red'),
    };
  }

  function hexAlpha(hex, a) {
    const r = parseInt(hex.slice(1,3),16), g2 = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return `rgba(${r},${g2},${b},${a})`;
  }


  // Animated counters
  document.querySelectorAll('[data-count]').forEach(el => {
    const target = parseInt(el.dataset.count, 10);
    if (!target) return;
    const steps = 40, dur = 800;
    let cur = 0;
    const t = setInterval(() => {
      cur = Math.min(cur + Math.ceil(target / steps), target);
      el.textContent = cur.toLocaleString();
      if (cur >= target) clearInterval(t);
    }, dur / steps);
  });

  // Comparison combo chart
  const DATA = __DATA_JSON__;

  function drawCompare() {    const C = getColors();
    const container = document.getElementById('chart-compare');
    if (!container || !DATA.length) return;
    const W = container.clientWidth || 800;
    const H = 200;
    const PAD = { t: 24, b: 48, l: 56, r: 52 };
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = DATA.length;

    const maxCost = Math.max(...DATA.map(d => d.cost), 0.001);
    const maxSess = Math.max(...DATA.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(6, slotW * 0.45);
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${PAD.l}" y1="${gy.toFixed(1)}" x2="${PAD.l+cW}" y2="${gy.toFixed(1)}"
              stroke="${C.border}" stroke-width="1" ${frac < 1 ? 'stroke-dasharray="3,3"' : ''}/>`;
      s += `<text x="${PAD.l-6}" y="${(gy+4).toFixed(1)}" text-anchor="end"
              fill="${C.accent}" font-size="10">$${(maxCost*frac).toFixed(2)}</text>`;
      s += `<text x="${PAD.l+cW+6}" y="${(gy+4).toFixed(1)}"
              fill="${C.green}" font-size="10">${Math.round(maxSess*frac)}</text>`;
    }
    s += `<line x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t+cH}" stroke="${C.border}" stroke-width="1"/>`;

    DATA.forEach((d, i) => {
      const bh = Math.max(2, d.cost / maxCost * cH);
      const bx = cx(i) - barW / 2;
      const fill = d.unlinked ? hexAlpha(C.yellow, 0.55) : hexAlpha(C.accent, 0.65);
      s += `<rect x="${bx.toFixed(1)}" y="${(PAD.t+cH-bh).toFixed(1)}"
              width="${barW.toFixed(1)}" height="${bh.toFixed(1)}"
              rx="2" fill="${fill}"/>`;
    });

    const pts = DATA.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {
        const slope = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = slope; m[i+1] = slope;
      }
      for (let i = 1; i < pts.length-1; i++) {
        m[i] = ((pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]) +
                (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0])) / 2;
      }
      let path = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      for (let i = 0; i < pts.length-1; i++) {
        const dx = (pts[i+1][0]-pts[i][0])/3;
        path += ` C ${(pts[i][0]+dx).toFixed(1)} ${(pts[i][1]+m[i]*dx).toFixed(1)} ${(pts[i+1][0]-dx).toFixed(1)} ${(pts[i+1][1]-m[i+1]*dx).toFixed(1)} ${pts[i+1][0].toFixed(1)} ${pts[i+1][1].toFixed(1)}`;
      }
      const fill = path + ` L ${pts[pts.length-1][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} L ${pts[0][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} Z`;
      s += `<path d="${fill}" fill="${hexAlpha(C.green, 0.08)}"/>`;
      s += `<path d="${path}" fill="none" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
      pts.forEach(([px,py]) => s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" fill="${C.green}"/>`);
    }

    DATA.forEach((d, i) => {
      const x = cx(i);
      s += `<text x="${x.toFixed(1)}" y="${H-28}" text-anchor="middle" fill="${C.muted}" font-size="11">${d.period}</text>`;
      const label = d.label.length > 18 ? d.label.slice(0,16)+'…' : d.label;
      s += `<text x="${x.toFixed(1)}" y="${H-10}" text-anchor="middle" fill="${C.muted}" font-size="9">${label}</text>`;
    });

    s += `<rect x="${PAD.l}" y="6" width="10" height="10" rx="2" fill="${hexAlpha(C.accent, 0.65)}"/>`;
    s += `<text x="${PAD.l+14}" y="15" fill="${C.accent}" font-size="10">Cost USD</text>`;
    s += `<line x1="${PAD.l+72}" y1="11" x2="${PAD.l+88}" y2="11" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
    s += `<circle cx="${PAD.l+80}" cy="11" r="3" fill="${C.green}"/>`;
    s += `<text x="${PAD.l+94}" y="15" fill="${C.green}" font-size="10">Sessions</text>`;

    s += '</svg>';
    container.innerHTML = s;
  }

  // ── Pie charts (models by project / by cost) ───────────────────────────
  const IDX_PIE_BY_PROJECT = __IDX_PIE_BY_PROJECT_JSON__;
  const IDX_PIE_BY_COST = __IDX_PIE_BY_COST_JSON__;

  function drawIdxPie(containerId, data, fmtVal) {
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) {
      container.innerHTML = `<p style="color:${C.muted};font-size:12px;padding:16px">No data</p>`;
      return;
    }
    const W = container.clientWidth || 500;
    const H = 200;
    const R = Math.min(H * 0.42, 78);
    const IR = R * 0.55;
    const CX = R + 16, CY = H / 2;
    const LEGEND_X = CX + R + 22;
    const total = data.reduce((s, d) => s + d.value, 0);
    if (total === 0) { container.innerHTML = `<p style="color:${C.muted};font-size:12px;padding:16px">No data</p>`; return; }
    const fmt = fmtVal || (v => v.toFixed(2));

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    let startAngle = -Math.PI / 2;
    const segments = data.map(d => {
      const sweep = (d.value / total) * 2 * Math.PI;
      const seg = { ...d, startAngle, sweep };
      startAngle += sweep;
      return seg;
    });

    function polarXY(angle, r) { return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)]; }

    segments.forEach(seg => {
      if (seg.sweep < 0.001) return;
      const [x1o, y1o] = polarXY(seg.startAngle, R);
      const [x2o, y2o] = polarXY(seg.startAngle + seg.sweep, R);
      const [x1i, y1i] = polarXY(seg.startAngle + seg.sweep, IR);
      const [x2i, y2i] = polarXY(seg.startAngle, IR);
      const large = seg.sweep > Math.PI ? 1 : 0;
      const path = `M ${x1o.toFixed(2)} ${y1o.toFixed(2)} A ${R} ${R} 0 ${large} 1 ${x2o.toFixed(2)} ${y2o.toFixed(2)} L ${x1i.toFixed(2)} ${y1i.toFixed(2)} A ${IR} ${IR} 0 ${large} 0 ${x2i.toFixed(2)} ${y2i.toFixed(2)} Z`;
      s += `<path d="${path}" fill="${seg.color}" opacity="0.85"><title>${seg.label}: ${fmt(seg.value)} (${Math.round(seg.value/total*100)}%)</title></path>`;
    });

    s += `<text x="${CX}" y="${CY - 6}" text-anchor="middle" fill="${C.text}" font-size="14" font-weight="700">${fmt(total)}</text>`;
    s += `<text x="${CX}" y="${CY + 10}" text-anchor="middle" fill="${C.muted}" font-size="9">total</text>`;

    const legendItemH = 18;
    const legendStartY = CY - (data.length * legendItemH) / 2;
    data.forEach((d, i) => {
      const ly = legendStartY + i * legendItemH + 8;
      if (ly > H - 4) return;
      const pct = Math.round(d.value / total * 100);
      s += `<rect x="${LEGEND_X}" y="${ly - 8}" width="10" height="10" rx="2" fill="${d.color}" opacity="0.85"/>`;
      s += `<text x="${LEGEND_X + 14}" y="${ly}" fill="${C.text}" font-size="11">${d.label}</text>`;
      s += `<text x="${W - 4}" y="${ly}" text-anchor="end" fill="${C.muted}" font-size="10">${pct}%</text>`;
    });
    s += '</svg>';
    container.innerHTML = s;
  }

  function fmtIdxCost(v) { return '$' + v.toFixed(2); }

  // ── Monthly totals chart ──────────────────────────────────────────────────
  const MONTHLY_DATA = __MONTHLY_DATA_JSON__;

  function drawMonthly() {
    const C = getColors();
    const container = document.getElementById('chart-monthly-total');
    if (!container || !MONTHLY_DATA.length) return;
    const W = container.clientWidth || 800;
    const H = 220;
    const PAD = { t: 24, b: 52, l: 56, r: 52 };
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = MONTHLY_DATA.length;

    const maxCost = Math.max(...MONTHLY_DATA.map(d => d.cost), 0.001);
    const maxSess = Math.max(...MONTHLY_DATA.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, Math.min(slotW * 0.55, 40));
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${PAD.l}" y1="${gy.toFixed(1)}" x2="${PAD.l+cW}" y2="${gy.toFixed(1)}"
              stroke="${C.border}" stroke-width="1" ${frac < 1 ? 'stroke-dasharray="3,3"' : ''}/>`;
      s += `<text x="${PAD.l-6}" y="${(gy+4).toFixed(1)}" text-anchor="end"
              fill="${C.accent}" font-size="10">$${(maxCost*frac).toFixed(2)}</text>`;
      s += `<text x="${PAD.l+cW+6}" y="${(gy+4).toFixed(1)}"
              fill="${C.green}" font-size="10">${Math.round(maxSess*frac)}</text>`;
    }
    s += `<line x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t+cH}" stroke="${C.border}" stroke-width="1"/>`;

    MONTHLY_DATA.forEach((d, i) => {
      const bx = cx(i) - barW / 2;
      const linkedCost = d.cost - d.unlinked_cost;
      const linkedH = Math.max(0, linkedCost / maxCost * cH);
      const unlinkedH = Math.max(0, d.unlinked_cost / maxCost * cH);
      const totalH = Math.max(2, linkedH + unlinkedH);
      const baseY = PAD.t + cH;
      // Linked portion (bottom, blue)
      if (linkedH > 0) {
        s += `<rect x="${bx.toFixed(1)}" y="${(baseY - linkedH).toFixed(1)}"
                width="${barW.toFixed(1)}" height="${linkedH.toFixed(1)}"
                rx="2" fill="${hexAlpha(C.accent, 0.70)}">
                <title>${d.period}: $${linkedCost.toFixed(2)} linked</title></rect>`;
      }
      // Unlinked portion (top, yellow)
      if (unlinkedH > 0) {
        s += `<rect x="${bx.toFixed(1)}" y="${(baseY - linkedH - unlinkedH).toFixed(1)}"
                width="${barW.toFixed(1)}" height="${unlinkedH.toFixed(1)}"
                rx="2" fill="${hexAlpha(C.yellow, 0.65)}">
                <title>${d.period}: $${d.unlinked_cost.toFixed(2)} unlinked</title></rect>`;
      }
      // Fallback bar if both are 0 but total > 0
      if (linkedH === 0 && unlinkedH === 0 && d.cost > 0) {
        s += `<rect x="${bx.toFixed(1)}" y="${(baseY - 2).toFixed(1)}"
                width="${barW.toFixed(1)}" height="2" rx="1"
                fill="${hexAlpha(C.accent, 0.70)}"/>`;
      }
    });

    const pts = MONTHLY_DATA.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {
        const slope = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = slope; m[i+1] = slope;
      }
      for (let i = 1; i < pts.length-1; i++) {
        m[i] = ((pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]) +
                (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0])) / 2;
      }
      let path = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      for (let i = 0; i < pts.length-1; i++) {
        const dx = (pts[i+1][0]-pts[i][0])/3;
        path += ` C ${(pts[i][0]+dx).toFixed(1)} ${(pts[i][1]+m[i]*dx).toFixed(1)} ${(pts[i+1][0]-dx).toFixed(1)} ${(pts[i+1][1]-m[i+1]*dx).toFixed(1)} ${pts[i+1][0].toFixed(1)} ${pts[i+1][1].toFixed(1)}`;
      }
      const fillPath = path + ` L ${pts[pts.length-1][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} L ${pts[0][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} Z`;
      s += `<path d="${fillPath}" fill="${hexAlpha(C.green, 0.08)}"/>`;
      s += `<path d="${path}" fill="none" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
      pts.forEach(([px,py], i) => s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" fill="${C.green}"><title>${MONTHLY_DATA[i].period}: ${MONTHLY_DATA[i].sessions} sessions</title></circle>`);
    }

    // X-axis labels — show every month, rotate if crowded
    const rotate = n > 12;
    MONTHLY_DATA.forEach((d, i) => {
      const x = cx(i);
      const label = d.period.slice(0, 7); // YYYY-MM
      if (rotate) {
        s += `<text x="${x.toFixed(1)}" y="${H - 4}" text-anchor="end"
                transform="rotate(-40,${x.toFixed(1)},${H-4})"
                fill="${C.muted}" font-size="9">${label}</text>`;
      } else {
        s += `<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="middle"
                fill="${C.muted}" font-size="10">${label}</text>`;
      }
    });

    s += `<rect x="${PAD.l}" y="6" width="10" height="10" rx="2" fill="${hexAlpha(C.accent, 0.70)}"/>`;
    s += `<text x="${PAD.l+14}" y="15" fill="${C.accent}" font-size="10">Linked cost</text>`;
    s += `<rect x="${PAD.l+88}" y="6" width="10" height="10" rx="2" fill="${hexAlpha(C.yellow, 0.65)}"/>`;
    s += `<text x="${PAD.l+102}" y="15" fill="${C.yellow}" font-size="10">Unlinked cost</text>`;
    s += `<line x1="${PAD.l+182}" y1="11" x2="${PAD.l+198}" y2="11" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
    s += `<circle cx="${PAD.l+190}" cy="11" r="3" fill="${C.green}"/>`;
    s += `<text x="${PAD.l+204}" y="15" fill="${C.green}" font-size="10">Sessions</text>`;

    s += '</svg>';
    container.innerHTML = s;
  }

  function redrawAll() {
    drawCompare();
    drawMonthly();
    drawIdxPie('idx-pie-by-project', IDX_PIE_BY_PROJECT, null);
    drawIdxPie('idx-pie-by-cost',    IDX_PIE_BY_COST,    fmtIdxCost);
  }
  redrawAll();
  window.addEventListener('resize', redrawAll);
</script>
"""

INDEX_BODY_TMPL = r"""
<div class="header">
  <div><h1>⬡ Copilot Cost — Projects Overview</h1></div>
  <button class="theme-btn" id="theme-btn" title="Toggle theme">☀</button>
</div>
<p class="meta">__META_LINE__</p>

__STATS_BLOCK__

<section>
  <h2>Cost &amp; Sessions by Project</h2>
  <div class="chart-panel">
    <div id="chart-compare" class="combo-chart"></div>
  </div>
</section>

<section>
  <h2>Total Cost &amp; Sessions by Month</h2>
  <div class="chart-panel">
    <div id="chart-monthly-total" class="combo-chart"></div>
  </div>
</section>

<div class="two-col">
  <div class="chart-panel">
    <h3>Models by Project (top model per project)</h3>
    <div id="idx-pie-by-project" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Cost (session cost attributed to top model)</h3>
    <div id="idx-pie-by-cost" class="pie-chart-wrap"></div>
  </div>
</div>

<div class="proj-grid">
__PROJECT_CARDS__
</div>
"""

PROJECT_CSS = r"""
  :root {
    --bg: #0a0e14; --surface: #111820; --surface2: #161d27;
    --border: #1f2d3d; --text: #cdd9e5; --muted: #636e7b;
    --accent: #58a6ff; --accent2: #79c0ff; --green: #3fb950;
    --yellow: #d29922; --red: #f85149; --unknown: #636e7b;
    --hm-0: #1e2530; --hm-1: #0e4429; --hm-2: #26a641; --hm-3: #39d353;
  }
  [data-theme="light"] {
    --bg: #f6f8fa; --surface: #ffffff; --surface2: #f0f2f4;
    --border: #d0d7de; --text: #1f2328; --muted: #656d76;
    --accent: #0969da; --accent2: #218bff; --green: #1a7f37;
    --yellow: #9a6700; --red: #d1242f; --unknown: #656d76;
    --hm-0: #ebedf0; --hm-1: #9be9a8; --hm-2: #40c463; --hm-3: #216e39;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px; padding: 24px; max-width: 1400px; margin: 0 auto; }

  /* ── Header ── */
  .header { display: flex; align-items: flex-start; justify-content: space-between;
    margin-bottom: 6px; flex-wrap: wrap; gap: 8px; }
  h1 { font-size: 22px; font-weight: 700; color: var(--accent);
    text-shadow: 0 0 20px rgba(88,166,255,0.4); letter-spacing: -0.3px; }
  .meta { color: var(--muted); margin-bottom: 20px; font-size: 12px; }
  .theme-btn { background: none; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); cursor: pointer; font-size: 16px; padding: 4px 10px;
    transition: border-color 0.2s, color 0.2s; }
  .theme-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ── Stat cards ── */
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px; margin-bottom: 28px; }
  .stat { background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px;
    box-shadow: 0 0 0 0 rgba(88,166,255,0); transition: box-shadow 0.3s;
    position: relative; overflow: hidden; }
  .stat::before { content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 2px; background: linear-gradient(90deg, transparent, var(--accent), transparent);
    opacity: 0.6; }
  .stat:hover { box-shadow: 0 0 16px rgba(88,166,255,0.15); }
  .stat-value { font-size: 28px; font-weight: 700; color: var(--accent);
    font-variant-numeric: tabular-nums; }
  .stat-label { font-size: 11px; color: var(--muted); margin-top: 4px;
    text-transform: uppercase; letter-spacing: 0.08em; }

  /* ── Sections ── */
  section { margin-bottom: 32px; }
  h2 { font-size: 13px; font-weight: 600; color: var(--muted);
    margin-bottom: 12px; padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    text-transform: uppercase; letter-spacing: 0.1em; }

  /* ── Tables ── */
  table { width: 100%; border-collapse: collapse; background: var(--surface);
    border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--border); }
  th { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); background: var(--bg); font-weight: 500; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(88,166,255,0.04); }
  .num { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, monospace; font-size: 13px; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; color: var(--muted); }

  /* ── Confidence dots ── */
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 5px; vertical-align: middle; }
  .dot-exact { background: var(--green); box-shadow: 0 0 5px var(--green); }
  .dot-low { background: var(--yellow); box-shadow: 0 0 5px var(--yellow); }
  .dot-unknown { background: var(--muted); }
  .conf-exact { color: var(--green); }
  .conf-low { color: var(--yellow); }
  .conf-unknown { color: var(--unknown); }

  /* ── Bar cells ── */
  .bar-wrap { position: relative; height: 20px; background: var(--surface2);
    border-radius: 3px; overflow: hidden; min-width: 80px; }
  .bar-fill { position: absolute; top: 0; left: 0; height: 100%;
    background: linear-gradient(90deg, rgba(88,166,255,0.5), rgba(88,166,255,0.25));
    border-radius: 3px; transition: width 0.8s ease; }
  .bar-label { position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
    font-size: 12px; font-family: ui-monospace, monospace;
    color: var(--text); white-space: nowrap; }

  /* ── Heatmap ── */
  .heatmap-wrap { background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 20px; }
  .heatmap-legend { display: flex; align-items: center; gap: 4px;
    margin-top: 8px; font-size: 11px; color: var(--muted); }
  .heatmap-legend span { display: inline-block; width: 11px; height: 11px; border-radius: 2px; }
  .hm-0 { fill: var(--hm-0); background: var(--hm-0); }
  .hm-1 { fill: var(--hm-1); background: var(--hm-1); }
  .hm-2 { fill: var(--hm-2); background: var(--hm-2); }
  .hm-3 { fill: var(--hm-3); background: var(--hm-3); }
  .hm-month-label { fill: var(--muted); }

  /* ── Layout grids ── */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 32px; }
  .three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; margin-bottom: 32px; }
  @media (max-width: 1100px) { .three-col { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 700px) { .two-col, .three-col { grid-template-columns: 1fr; } }

  /* ── Warning ── */
  .warning { background: color-mix(in srgb, var(--yellow) 15%, var(--bg)); border: 1px solid var(--yellow);
    border-radius: 6px; padding: 10px 16px; margin-bottom: 20px;
    color: var(--yellow); font-size: 13px; }

  /* ── Chart panels ── */
  .chart-panel { background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px; overflow-x: auto; }
  .chart-panel h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 12px; }
  .combo-chart { width: 100%; min-height: 180px; }
  .combo-chart svg { display: block; width: 100%; }

  /* ── Timeline chart ── */
  #timeline-chart { width: 100%; height: 220px; position: relative;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden; cursor: crosshair; }
  #timeline-chart svg { width: 100%; height: 100%; display: block; }
  #tl-tooltip { position: fixed; display: none; pointer-events: none;
    background: var(--surface); border: 1px solid var(--accent);
    border-radius: 6px; padding: 10px 14px; font-size: 12px; line-height: 1.6;
    box-shadow: 0 4px 20px rgba(0,0,0,0.6), 0 0 10px rgba(88,166,255,0.15);
    max-width: 300px; z-index: 999; }
  .tt-id   { font-family: ui-monospace,monospace; color: var(--accent2); font-weight:600; }
  .tt-key  { color: var(--muted); font-size: 11px; }
  .tt-val  { color: var(--text); }
  .tt-conf-exact   { color: var(--green); }
  .tt-conf-low     { color: var(--yellow); }
  .tt-conf-unknown { color: var(--muted); }
  .tl-axis-label { font-size: 10px; fill: var(--muted); font-family: ui-monospace,monospace; }

  /* ── Model Intelligence cards ── */
  .model-cards { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 4px; }
  .model-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 18px; min-width: 200px; flex: 1; position: relative; overflow: hidden;
  }
  .model-card::before { content: ""; position: absolute; top: 0; left: 0; right: 0;
    height: 3px; }
  .model-card-name { font-size: 15px; font-weight: 700; margin-bottom: 10px; }
  .model-card-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
  .mc-val { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .mc-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }

  /* ── Model tag in issue table ── */
  .model-cell { white-space: nowrap; }
  .model-tag { font-size: 11px; font-family: ui-monospace,monospace;
    background: rgba(88,166,255,0.12); border-radius: 3px; padding: 1px 5px;
    color: var(--accent2); }
  .model-share { font-size: 10px; color: var(--muted); margin-left: 4px; }

  /* ── Token combo chart ── */
  .tok-combo-chart { width: 100%; min-height: 180px; }

  /* ── Pie charts ── */
  .pie-chart-wrap { width: 100%; min-height: 200px; }
  .pie-chart-wrap svg { display: block; width: 100%; }
"""

PROJECT_JS = r"""
<script>
  // ── Theme ────────────────────────────────────────────────────────────────
  (function initTheme() {
    const stored = localStorage.getItem('copilot-cost-theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = stored || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '🌙';
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
      if (!localStorage.getItem('copilot-cost-theme')) {
        const t = e.matches ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', t);
        if (btn) btn.textContent = t === 'dark' ? '☀' : '🌙';
        if (typeof redrawAll === 'function') redrawAll();
      }
    });
    if (btn) btn.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('copilot-cost-theme', next);
      btn.textContent = next === 'dark' ? '☀' : '🌙';
      if (typeof redrawAll === 'function') redrawAll();
    });
  })();

  function getColors() {
    const s = getComputedStyle(document.documentElement);
    const g = n => s.getPropertyValue(n).trim();
    return {
      bg: g('--bg'), surface: g('--surface'), surface2: g('--surface2'),
      border: g('--border'), text: g('--text'), muted: g('--muted'),
      accent: g('--accent'), accent2: g('--accent2'), green: g('--green'),
      yellow: g('--yellow'), red: g('--red'),
    };
  }

  function hexAlpha(hex, a) {
    const r = parseInt(hex.slice(1,3),16), g2 = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return `rgba(${r},${g2},${b},${a})`;
  }


  // Animated counters
  document.querySelectorAll('[data-count]').forEach(el => {
    const target = parseInt(el.dataset.count, 10);
    if (isNaN(target) || target === 0) { el.textContent = '0'; return; }
    const duration = 800, steps = 40, step = Math.ceil(target / steps);
    let current = 0;
    const timer = setInterval(() => {
      current = Math.min(current + step, target);
      el.textContent = current.toLocaleString();
      if (current >= target) clearInterval(timer);
    }, duration / steps);
  });

  // ── Timeline bar chart ──────────────────────────────────────────────────
  const SESSIONS = __SESSIONS_JSON__;

  function fmtCost(v) {
    if (v == null) return '—';
    return '$' + v.toFixed(3);
  }

  function renderTimeline() {
    const C = getColors();
    const CONF_COLOR = {
      exact:   hexAlpha(C.green, 0.85),
      low:     hexAlpha(C.yellow, 0.85),
      unknown: hexAlpha(C.muted, 0.6),
    };
    const CONF_COLOR_HOVER = {
      exact:   C.green,
      low:     C.yellow,
      unknown: C.muted,
    };
    const container = document.getElementById('timeline-chart');
    const W = container.clientWidth || 1200;
    const H = 220;
    const PAD = { t: 20, b: 28, l: 8, r: 8 };
    const chartH = H - PAD.t - PAD.b;
    const chartW = W - PAD.l - PAD.r;

    const SLOT = 8;
    const BAR_W = 5;
    const maxVisible = Math.floor(chartW / SLOT);
    const allSess = SESSIONS.slice()
      .filter(s => s.cost != null)
      .sort((a,b) => (a.date||'').localeCompare(b.date||''));
    const sess = allSess.slice(-maxVisible);
    const n = sess.length;
    if (n === 0) return;

    const maxCost = Math.max(...sess.map(s => s.cost || 0), 0.001);
    const maxPrem = Math.max(...sess.map(s => s.premium || 0), 1);

    function barHeight(s) {
      if (s.cost != null) return Math.max(3, Math.round(s.cost / maxCost * chartH));
      if (s.premium != null) return Math.max(2, Math.round(s.premium / maxPrem * chartH * 0.4));
      return 2;
    }

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    let html = `<svg id="tl-svg" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">`;
    html += `<line x1="${PAD.l}" y1="${H-PAD.b}" x2="${W-PAD.r}" y2="${H-PAD.b}" stroke="${C.border}" stroke-width="1"/>`;

    const gridY = PAD.t + Math.round(chartH * 0.5);
    html += `<line x1="${PAD.l}" y1="${gridY}" x2="${W-PAD.r}" y2="${gridY}" stroke="${C.border}" stroke-width="1" stroke-dasharray="3,3"/>`;

    sess.forEach((s, i) => {
      const x = PAD.l + i * SLOT;
      const bh = barHeight(s);
      const y = H - PAD.b - bh;
      const color = CONF_COLOR[s.conf] || CONF_COLOR.unknown;
      html += `<rect class="tl-bar" data-idx="${i}" x="${x}" y="${y}" width="${BAR_W}" height="${bh}" rx="1" fill="${color}" opacity="0.9"/>`;
    });

    let lastLabelX = -999;
    let lastYM = '';
    sess.forEach((s, i) => {
      if (!s.date) return;
      const ym = s.date.slice(0, 7);
      if (ym === lastYM) return;
      lastYM = ym;
      const x = PAD.l + i * SLOT + BAR_W / 2;
      if (x - lastLabelX < 60) return;
      const [yr, mo] = ym.split('-');
      const label = MONTHS[parseInt(mo,10)-1] + " '" + yr.slice(2);
      html += `<line x1="${x}" y1="${H-PAD.b}" x2="${x}" y2="${H-PAD.b+4}" stroke="${C.muted}" stroke-width="1"/>`;
      html += `<text class="tl-axis-label" x="${x}" y="${H-4}" text-anchor="middle">${label}</text>`;
      lastLabelX = x;
    });

    const hiddenCount = allSess.length - n;
    html += `<text class="tl-axis-label" x="${PAD.l+2}" y="${PAD.t-4}">max ${fmtCost(maxCost)}</text>`;
    if (hiddenCount > 0) {
      html += `<text class="tl-axis-label" x="${W-PAD.r-2}" y="${PAD.t-4}" text-anchor="end">showing last ${n} of ${allSess.length} sessions</text>`;
    }

    html += '</svg>';
    container.innerHTML = html + '<div id="tl-tooltip"></div>';

    const newSvg = container.querySelector('svg');
    if (!newSvg) return;
    const tt = document.getElementById('tl-tooltip');

    newSvg.querySelectorAll('.tl-bar').forEach(bar => {
      const s = sess[parseInt(bar.dataset.idx, 10)];
      bar.addEventListener('mouseenter', (e) => {
        const conf = s.conf || 'unknown';
        const confColor = CONF_COLOR_HOVER[conf] || C.muted;
        bar.setAttribute('opacity', '1');
        bar.setAttribute('fill', confColor);
        const issues = s.issues && s.issues.length ? s.issues.join(', ') : '—';
        const modelLines = (s.top_models && s.top_models.length)
          ? s.top_models.map((m, i) =>
              `<div><span class="tt-key">${i === 0 ? 'Models&nbsp;&nbsp;' : '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;'}</span><span class="tt-val" style="color:${C.accent}">${m.name}</span><span style="color:${C.muted};font-size:10px"> ${m.share}%</span></div>`
            ).join('')
          : '';
        const fmtTok = v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : String(v);
        const tokLine = s.tokens != null ? `<div><span class="tt-key">Tokens&nbsp;&nbsp;</span><span class="tt-val">${fmtTok(s.tokens)}</span></div>` : '';
        tt.innerHTML =
          `<div class="tt-id">${s.id}</div>` +
          `<div><span class="tt-key">Date&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${s.date || '—'}</span></div>` +
          `<div><span class="tt-key">Repo&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${s.repo}</span></div>` +
          `<div><span class="tt-key">Branch&nbsp;&nbsp;</span><span class="tt-val">${s.branch}</span></div>` +
          `<div><span class="tt-key">Premium&nbsp;</span><span class="tt-val">${s.premium != null ? s.premium : '—'}</span></div>` +
          `<div><span class="tt-key">Cost&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-val">${fmtCost(s.cost)}</span></div>` +
          `<div><span class="tt-key">Conf&nbsp;&nbsp;&nbsp;&nbsp;</span><span class="tt-conf-${conf}">${conf}</span></div>` +
          `<div><span class="tt-key">Issues&nbsp;&nbsp;</span><span class="tt-val">${issues}</span></div>` +
          modelLines + tokLine;
        tt.style.display = 'block';
        moveTooltip(e);
      });
      bar.addEventListener('mousemove', moveTooltip);
      bar.addEventListener('mouseleave', () => {
        bar.setAttribute('opacity', '0.9');
        bar.setAttribute('fill', CONF_COLOR[s.conf] || CONF_COLOR.unknown);
        tt.style.display = 'none';
      });
    });

    function moveTooltip(e) {
      const vpW = window.innerWidth, vpH = window.innerHeight;
      const ttW = 280, ttH = 170;
      let x = e.clientX + 16, y = e.clientY + 16;
      if (x + ttW > vpW - 8) x = e.clientX - ttW - 8;
      if (y + ttH > vpH - 8) y = e.clientY - ttH - 8;
      tt.style.left = x + 'px';
      tt.style.top  = y + 'px';
    }
  }

  // ── Combo charts (cost bars + sessions curve) ─────────────────────────
  const MONTHLY_DATA = __MONTHLY_DATA_JSON__;
  const WEEKLY_DATA = __WEEKLY_DATA_JSON__;
  const YEARLY_DATA = __YEARLY_DATA_JSON__;

  function drawComboChart(containerId, data) {
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container || !data.length) return;

    const W = container.clientWidth || 600;
    const H = 180;
    const PAD = { t: 24, b: 32, l: 56, r: 52 };
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = data.length;

    const maxCost = Math.max(...data.map(d => d.cost), 0.001);
    const maxSess = Math.max(...data.map(d => d.sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, slotW * 0.5);
    const cx = i => PAD.l + i * slotW + slotW / 2;
    const sessY  = v => PAD.t + cH - (v / maxSess * cH);
    const fmtC   = v => '$' + v.toFixed(2);
    const fmtS   = v => String(Math.round(v));

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${PAD.l}" y1="${gy.toFixed(1)}" x2="${PAD.l+cW}" y2="${gy.toFixed(1)}"
              stroke="${C.border}" stroke-width="1" ${frac < 1 ? 'stroke-dasharray="3,3"' : ''}/>`;
      s += `<text x="${PAD.l-6}" y="${(gy+4).toFixed(1)}" text-anchor="end"
              fill="${C.accent}" font-size="10">${fmtC(maxCost * frac)}</text>`;
      s += `<text x="${PAD.l+cW+6}" y="${(gy+4).toFixed(1)}"
              fill="${C.green}" font-size="10">${fmtS(maxSess * frac)}</text>`;
    }

    s += `<line x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t+cH}"
            stroke="${C.border}" stroke-width="1"/>`;

    data.forEach((d, i) => {
      const bh = Math.max(2, d.cost / maxCost * cH);
      const bx = cx(i) - barW / 2;
      const by = PAD.t + cH - bh;
      s += `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}"
              width="${barW.toFixed(1)}" height="${bh.toFixed(1)}"
              rx="2" fill="${hexAlpha(C.accent, 0.65)}"/>`;
    });

    const pts = data.map((d, i) => [cx(i), sessY(d.sessions)]);
    if (pts.length >= 2) {
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {
        const dx = pts[i+1][0] - pts[i][0];
        const dy = pts[i+1][1] - pts[i][1];
        const slope = dy / dx;
        if (i === 0) m[0] = slope;
        else if (i === pts.length - 2) m[pts.length-1] = slope;
        m[i === 0 ? 0 : i] = slope;
        m[i+1] = slope;
      }
      for (let i = 1; i < pts.length - 1; i++) {
        const s0 = (pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]);
        const s1 = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = (s0 + s1) / 2;
      }
      let path = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const dx = (pts[i+1][0] - pts[i][0]) / 3;
        const cp1x = pts[i][0] + dx, cp1y = pts[i][1] + m[i] * dx;
        const cp2x = pts[i+1][0] - dx, cp2y = pts[i+1][1] - m[i+1] * dx;
        path += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${pts[i+1][0].toFixed(1)} ${pts[i+1][1].toFixed(1)}`;
      }
      const fillPath = path +
        ` L ${pts[pts.length-1][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)}` +
        ` L ${pts[0][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} Z`;
      s += `<path d="${fillPath}" fill="${hexAlpha(C.green, 0.08)}"/>`;
      s += `<path d="${path}" fill="none" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
      pts.forEach(([px, py]) => {
        s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" fill="${C.green}"/>`;
      });
    }

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function fmtPeriod(p) {
      const wm = p.match(/^\d{4}-W(\d+)$/);
      if (wm) return 'W' + wm[1];
      const mm = p.match(/^\d{4}-(\d{2})$/);
      if (mm) return MONTHS[parseInt(mm[1], 10) - 1] || p;
      return p;
    }
    data.forEach((d, i) => {
      s += `<text x="${cx(i).toFixed(1)}" y="${H-6}" text-anchor="middle"
              fill="${C.muted}" font-size="10">${fmtPeriod(d.period)}</text>`;
    });

    s += `<rect x="${PAD.l}" y="6" width="10" height="10" rx="2" fill="${hexAlpha(C.accent, 0.65)}"/>`;
    s += `<text x="${PAD.l+14}" y="15" fill="${C.accent}" font-size="10">Cost USD</text>`;
    s += `<line x1="${PAD.l+72}" y1="11" x2="${PAD.l+88}" y2="11" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
    s += `<circle cx="${PAD.l+80}" cy="11" r="3" fill="${C.green}"/>`;
    s += `<text x="${PAD.l+94}" y="15" fill="${C.green}" font-size="10">Sessions</text>`;
    s += '</svg>';
    container.innerHTML = s;
  }

  function drawAllCombos() {
    drawComboChart('chart-monthly', MONTHLY_DATA);
    drawComboChart('chart-weekly',  WEEKLY_DATA);
    drawComboChart('chart-yearly',  YEARLY_DATA);
    drawTokenChart('chart-tok-monthly', MONTHLY_DATA);
    drawTokenChart('chart-tok-weekly',  WEEKLY_DATA);
    drawTokenChart('chart-tok-yearly',  YEARLY_DATA);
  }
  // ── Token combo charts (total_tokens bars + token_sessions curve) ────────
  function drawTokenChart(containerId, data) {
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    const filtered = data.filter(d => d.total_tokens > 0);
    if (!filtered.length) { container.innerHTML = `<p style="color:${C.muted};font-size:12px;padding:16px">No token data</p>`; return; }

    const W = container.clientWidth || 600;
    const H = 180;
    const PAD = { t: 24, b: 32, l: 64, r: 52 };
    const cW = W - PAD.l - PAD.r;
    const cH = H - PAD.t - PAD.b;
    const n = filtered.length;

    const maxTok  = Math.max(...filtered.map(d => d.total_tokens), 1);
    const maxSess = Math.max(...filtered.map(d => d.token_sessions), 1);
    const slotW = cW / n;
    const barW  = Math.max(4, slotW * 0.5);
    const cx    = i => PAD.l + i * slotW + slotW / 2;
    const sessY = v => PAD.t + cH - (v / maxSess * cH);
    const fmtTok = v => v >= 1e9 ? (v/1e9).toFixed(1)+'B' : v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'K' : String(v);

    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function fmtPeriod(p) {
      const wm = p.match(/^\d{4}-W(\d+)$/);
      if (wm) return 'W' + wm[1];
      const mm = p.match(/^\d{4}-(\d{2})$/);
      if (mm) return MONTHS[parseInt(mm[1], 10) - 1] || p;
      return p;
    }

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    for (const frac of [0.25, 0.5, 0.75, 1.0]) {
      const gy = PAD.t + cH * (1 - frac);
      s += `<line x1="${PAD.l}" y1="${gy.toFixed(1)}" x2="${PAD.l+cW}" y2="${gy.toFixed(1)}"
              stroke="${C.border}" stroke-width="1" ${frac < 1 ? 'stroke-dasharray="3,3"' : ''}/>`;
      s += `<text x="${PAD.l-6}" y="${(gy+4).toFixed(1)}" text-anchor="end"
              fill="${C.yellow}" font-size="10">${fmtTok(maxTok * frac)}</text>`;
      s += `<text x="${PAD.l+cW+6}" y="${(gy+4).toFixed(1)}"
              fill="${C.green}" font-size="10">${Math.round(maxSess * frac)}</text>`;
    }
    s += `<line x1="${PAD.l}" y1="${PAD.t}" x2="${PAD.l}" y2="${PAD.t+cH}" stroke="${C.border}" stroke-width="1"/>`;

    filtered.forEach((d, i) => {
      const bh = Math.max(2, d.total_tokens / maxTok * cH);
      const bx = cx(i) - barW / 2;
      const by = PAD.t + cH - bh;
      s += `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${hexAlpha(C.yellow, 0.65)}"/>`;
    });

    const pts = filtered.map((d, i) => [cx(i), sessY(d.token_sessions)]);
    if (pts.length >= 2) {
      const m = pts.map(() => 0);
      for (let i = 0; i < pts.length - 1; i++) {
        const sl = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = sl; m[i+1] = sl;
      }
      for (let i = 1; i < pts.length - 1; i++) {
        const s0 = (pts[i][1]-pts[i-1][1])/(pts[i][0]-pts[i-1][0]);
        const s1 = (pts[i+1][1]-pts[i][1])/(pts[i+1][0]-pts[i][0]);
        m[i] = (s0 + s1) / 2;
      }
      let path = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      for (let i = 0; i < pts.length - 1; i++) {
        const dx = (pts[i+1][0] - pts[i][0]) / 3;
        const cp1x = pts[i][0] + dx, cp1y = pts[i][1] + m[i] * dx;
        const cp2x = pts[i+1][0] - dx, cp2y = pts[i+1][1] - m[i+1] * dx;
        path += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${pts[i+1][0].toFixed(1)} ${pts[i+1][1].toFixed(1)}`;
      }
      const fillPath = path + ` L ${pts[pts.length-1][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} L ${pts[0][0].toFixed(1)} ${(PAD.t+cH).toFixed(1)} Z`;
      s += `<path d="${fillPath}" fill="${hexAlpha(C.green, 0.08)}"/>`;
      s += `<path d="${path}" fill="none" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
      pts.forEach(([px, py]) => s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="3" fill="${C.green}"/>`);
    }

    filtered.forEach((d, i) => {
      s += `<text x="${cx(i).toFixed(1)}" y="${H-6}" text-anchor="middle" fill="${C.muted}" font-size="10">${fmtPeriod(d.period)}</text>`;
    });

    s += `<rect x="${PAD.l}" y="6" width="10" height="10" rx="2" fill="${hexAlpha(C.yellow, 0.65)}"/>`;
    s += `<text x="${PAD.l+14}" y="15" fill="${C.yellow}" font-size="10">Tokens</text>`;
    s += `<line x1="${PAD.l+60}" y1="11" x2="${PAD.l+76}" y2="11" stroke="${hexAlpha(C.green, 0.8)}" stroke-width="2"/>`;
    s += `<circle cx="${PAD.l+68}" cy="11" r="3" fill="${C.green}"/>`;
    s += `<text x="${PAD.l+82}" y="15" fill="${C.green}" font-size="10">Sessions w/ Metrics</text>`;
    s += '</svg>';
    container.innerHTML = s;
  }

  // ── Model Intelligence cards ────────────────────────────────────────────
  const MODEL_DATA = __MODEL_DATA_JSON__;
  const MODEL_COLORS = { claude: '#58a6ff', gpt: '#3fb950', gemini: '#d29922' };
  function modelColor(id) {
    const lo = (id || '').toLowerCase();
    for (const [pfx, col] of Object.entries(MODEL_COLORS)) {
      if (lo.startsWith(pfx)) return col;
    }
    return '#8b949e';
  }
  function fmtTokGlobal(v) {
    if (v >= 1e9) return (v/1e9).toFixed(2)+'B';
    if (v >= 1e6) return (v/1e6).toFixed(2)+'M';
    if (v >= 1e3) return (v/1e3).toFixed(1)+'K';
    return String(v);
  }
  (function renderModelCards() {
    const container = document.getElementById('model-cards-container');
    if (!container || !MODEL_DATA.length) return;
    let html = '';
    MODEL_DATA.forEach(m => {
      const col = modelColor(m.model_id);
      const totalTok = m.total_tokens || 0;
      const cacheRatio = m.cache_read_tokens && (m.input_tokens + m.cache_read_tokens) > 0
        ? Math.round(m.cache_read_tokens / (m.input_tokens + m.cache_read_tokens) * 100) : 0;
      const premPct = m.requests_count > 0 ? Math.round(m.requests_premium / m.requests_count * 100) : 0;
      html += `
      <div class="model-card">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;background:${col}"></div>
        <div class="model-card-name" style="color:${col}">${m.display_name}</div>
        <div class="model-card-stats">
          <div><div class="mc-val" style="color:${col}">${m.requests_count.toLocaleString()}</div><div class="mc-label">Requests</div></div>
          <div><div class="mc-val">${premPct}%</div><div class="mc-label">Premium %</div></div>
          <div><div class="mc-val">${fmtTokGlobal(totalTok)}</div><div class="mc-label">Tokens (in+out)</div></div>
          <div><div class="mc-val">${cacheRatio}%</div><div class="mc-label">Cache Hit</div></div>
          <div><div class="mc-val">${m.sessions}</div><div class="mc-label">Sessions</div></div>
        </div>
      </div>`;
    });
    container.innerHTML = html;
  })();

  // ── Pie charts (models by sessions / issues / cost) ────────────────────
  const PIE_SESSIONS = __PIE_SESSIONS_JSON__;
  const PIE_ISSUES = __PIE_ISSUES_JSON__;
  const PIE_COST = __PIE_COST_JSON__;

  function drawPie(containerId, data, fmtVal) {
    const C = getColors();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!data || !data.length) {
      container.innerHTML = `<p style="color:${C.muted};font-size:12px;padding:16px">No data</p>`;
      return;
    }
    const W = container.clientWidth || 360;
    const H = 200;
    const R = Math.min(H * 0.42, 78);
    const IR = R * 0.55;
    const CX = R + 16, CY = H / 2;
    const LEGEND_X = CX + R + 22;

    const total = data.reduce((s, d) => s + d.value, 0);
    if (total === 0) {
      container.innerHTML = `<p style="color:${C.muted};font-size:12px;padding:16px">No data</p>`;
      return;
    }

    let s = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
      style="font-family:ui-monospace,monospace;overflow:visible">`;

    let startAngle = -Math.PI / 2;
    const segments = data.map(d => {
      const sweep = (d.value / total) * 2 * Math.PI;
      const seg = { ...d, startAngle, sweep };
      startAngle += sweep;
      return seg;
    });

    function polarXY(angle, r) {
      return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)];
    }

    segments.forEach(seg => {
      if (seg.sweep < 0.001) return;
      const [x1o, y1o] = polarXY(seg.startAngle, R);
      const [x2o, y2o] = polarXY(seg.startAngle + seg.sweep, R);
      const [x1i, y1i] = polarXY(seg.startAngle + seg.sweep, IR);
      const [x2i, y2i] = polarXY(seg.startAngle, IR);
      const large = seg.sweep > Math.PI ? 1 : 0;
      const path =
        `M ${x1o.toFixed(2)} ${y1o.toFixed(2)}` +
        ` A ${R} ${R} 0 ${large} 1 ${x2o.toFixed(2)} ${y2o.toFixed(2)}` +
        ` L ${x1i.toFixed(2)} ${y1i.toFixed(2)}` +
        ` A ${IR} ${IR} 0 ${large} 0 ${x2i.toFixed(2)} ${y2i.toFixed(2)} Z`;
      s += `<path d="${path}" fill="${seg.color}" opacity="0.85">
        <title>${seg.label}: ${fmtVal ? fmtVal(seg.value) : seg.value} (${Math.round(seg.value/total*100)}%)</title>
      </path>`;
    });

    s += `<text x="${CX}" y="${CY - 6}" text-anchor="middle" fill="${C.text}" font-size="14" font-weight="700">${fmtVal ? fmtVal(total) : total}</text>`;
    s += `<text x="${CX}" y="${CY + 10}" text-anchor="middle" fill="${C.muted}" font-size="9">total</text>`;

    const legendItemH = 18;
    const legendStartY = CY - (data.length * legendItemH) / 2;
    data.forEach((d, i) => {
      const ly = legendStartY + i * legendItemH + 8;
      if (ly > H - 4) return;
      const pct = Math.round(d.value / total * 100);
      s += `<rect x="${LEGEND_X}" y="${ly - 8}" width="10" height="10" rx="2" fill="${d.color}" opacity="0.85"/>`;
      s += `<text x="${LEGEND_X + 14}" y="${ly}" fill="${C.text}" font-size="11">${d.label}</text>`;
      s += `<text x="${W - 4}" y="${ly}" text-anchor="end" fill="${C.muted}" font-size="10">${pct}%</text>`;
    });

    s += '</svg>';
    container.innerHTML = s;
  }

  function fmtCostPie(v) { return '$' + v.toFixed(2); }

  function redrawAll() {
    renderTimeline();
    drawAllCombos();
    drawPie('pie-models-sessions', PIE_SESSIONS, null);
    drawPie('pie-models-issues',   PIE_ISSUES,   null);
    drawPie('pie-models-cost',     PIE_COST,     fmtCostPie);
  }
  redrawAll();
  window.addEventListener('resize', redrawAll);
</script>
"""

PROJECT_BODY_TMPL = r"""
<div class="header">
  <div>
    <h1>⬡ __TITLE__</h1>
  </div>
  <button class="theme-btn" id="theme-btn" title="Toggle theme">☀</button>
</div>
<p class="meta">__META_LINE__</p>

__STATS_BLOCK__

<section>
  <h2>Activity — Last 13 Weeks</h2>
  <div class="heatmap-wrap">
    __HEATMAP_SVG__
    <div class="heatmap-legend">
      Less &nbsp;
      <span class="hm-0"></span>
      <span class="hm-1"></span>
      <span class="hm-2"></span>
      <span class="hm-3"></span>
      &nbsp; More
    </div>
  </div>
</section>

<div class="three-col">
  <div class="chart-panel">
    <h3>Monthly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-monthly" class="combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Weekly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-weekly" class="combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Yearly — Cost (bars) &amp; Sessions (line)</h3>
    <div id="chart-yearly" class="combo-chart"></div>
  </div>
</div>

<div class="three-col">
  <div class="chart-panel">
    <h3>Monthly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-monthly" class="tok-combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Weekly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-weekly" class="tok-combo-chart"></div>
  </div>
  <div class="chart-panel">
    <h3>Yearly — Tokens (bars) &amp; Sessions w/ Metrics (line)</h3>
    <div id="chart-tok-yearly" class="tok-combo-chart"></div>
  </div>
</div>

<section id="model-intelligence">
  <h2>Model Intelligence</h2>
  <div class="model-cards" id="model-cards-container"></div>
</section>

<div class="three-col">
  <div class="chart-panel">
    <h3>Models by Sessions</h3>
    <div id="pie-models-sessions" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Issues</h3>
    <div id="pie-models-issues" class="pie-chart-wrap"></div>
  </div>
  <div class="chart-panel">
    <h3>Models by Cost (attributed)</h3>
    <div id="pie-models-cost" class="pie-chart-wrap"></div>
  </div>
</div>

<div class="two-col">
  <section>
    <h2>Top 10 Issues — Premium Requests</h2>
    <table><thead><tr>
<th>Issue</th><th>Sessions</th>
<th>Premium Req</th><th>Est Cost USD</th><th>Confidence</th><th>Top Model</th>
</tr></thead><tbody>__TOP_ISSUES_PREMIUM_ROWS__</tbody></table>
  </section>
  <section>
    <h2>Top 10 Issues — Estimated Cost</h2>
    <table><thead><tr>
<th>Issue</th><th>Sessions</th>
<th>Premium Req</th><th>Est Cost USD</th><th>Confidence</th><th>Top Model</th>
</tr></thead><tbody>__TOP_ISSUES_COST_ROWS__</tbody></table>
  </section>
</div>

<section><h2>By Repository / Branch</h2>
<table><thead><tr>
<th>Repository</th><th>Branch</th><th>Sessions</th>
<th>Premium Req</th><th>Est Cost USD</th><th>Top Model</th>
</tr></thead><tbody>__REPO_BRANCH_ROWS__</tbody></table></section>

<section>
  <h2>__TIMELINE_HEADING__</h2>
  <div id="timeline-chart"><svg id="tl-svg"></svg></div>
</section>

"""

# -- Data access (index / projects overview) -----------------------------------

_UNLINKED_PROJECT_SQL = "SELECT id FROM projects WHERE is_unlinked = TRUE ORDER BY id LIMIT 1"


def _effective_project_id_expr() -> str:
    """SQL expr resolving a run's *effective* project: falls back to the
    single 'Unlinked Sessions' project row when a run has no project_id (or
    points at a project itself flagged is_unlinked), so every session always
    renders under some project card -- matching the reference's treatment of
    unclassified sessions.
    """
    return (
        "COALESCE(pr.project_id, (SELECT id FROM projects WHERE is_unlinked LIMIT 1))"
    )


def fetch_index() -> dict:
    eff = _effective_project_id_expr()
    projects = run_query(
        f"""SELECT p.id, p.number, p.slug, p.title, p.is_unlinked,
                   COUNT(ri.*) AS sessions,
                   COALESCE(SUM(ri.cost_usd),0) AS cost_usd,
                   COUNT(ri.cost_usd) AS items_with_cost,
                   COALESCE(SUM(ri.premium_requests),0) AS premium_requests,
                   COUNT(ri.premium_requests) AS items_with_premium,
                   COUNT(DISTINCT (ri.issue_repo || '#' || ri.issue_number))
                       FILTER (WHERE ri.issue_number IS NOT NULL) AS issues
            FROM projects p
            LEFT JOIN pipeline_runs pr ON {eff} = p.id
            LEFT JOIN pipeline_run_items ri ON ri.run_id = pr.id
            GROUP BY p.id
            ORDER BY cost_usd DESC, sessions DESC, p.is_unlinked ASC"""
    )

    monthly = run_query(
        f"""SELECT to_char(date_trunc('month', ri.started_at), 'YYYY-MM') AS period,
                   COALESCE(SUM(ri.cost_usd),0) AS cost,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(ri.cost_usd) FILTER (WHERE p.is_unlinked), 0) AS unlinked_cost
            FROM pipeline_run_items ri
            JOIN pipeline_runs pr ON pr.id = ri.run_id
            LEFT JOIN projects p ON p.id = {eff}
            WHERE ri.started_at IS NOT NULL
            GROUP BY 1 ORDER BY 1"""
    )

    pie_by_project = run_query(
        f"""WITH model_counts AS (
                SELECT {eff} AS project_id, ri.model, COUNT(*) AS cnt
                FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
                WHERE ri.model IS NOT NULL
                GROUP BY {eff}, ri.model
            ), ranked AS (
                SELECT project_id, model, cnt,
                       ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY cnt DESC) AS rn
                FROM model_counts
            )
            SELECT model, COUNT(*) AS value FROM ranked WHERE rn = 1
            GROUP BY model ORDER BY value DESC"""
    )

    pie_by_cost = run_query(
        """SELECT model, COALESCE(SUM(cost_usd),0) AS value
           FROM pipeline_run_items
           WHERE model IS NOT NULL
           GROUP BY model
           HAVING COALESCE(SUM(cost_usd),0) > 0
           ORDER BY value DESC"""
    )

    return {
        "projects": [
            {
                "id": _num(p["id"], int),
                "number": _opt(p["number"]),
                "slug": p["slug"],
                "title": p["title"],
                "is_unlinked": p["is_unlinked"] == "t",
                "sessions": _num(p["sessions"], int),
                "cost_usd": _num(p["cost_usd"]),
                "items_with_cost": _num(p["items_with_cost"], int),
                "premium_requests": _num(p["premium_requests"], int),
                "items_with_premium": _num(p["items_with_premium"], int),
                "issues": _num(p["issues"], int),
            }
            for p in projects
        ],
        "monthly": [
            {
                "period": m["period"],
                "cost": _num(m["cost"]),
                "sessions": _num(m["sessions"], int),
                "unlinked_cost": _num(m["unlinked_cost"]),
            }
            for m in monthly
        ],
        "pie_by_project": [
            {"label": model_display_name(r["model"]), "value": _num(r["value"], int), "color": model_color(r["model"])}
            for r in pie_by_project
        ],
        "pie_by_cost": [
            {"label": model_display_name(r["model"]), "value": round(_num(r["value"]), 4), "color": model_color(r["model"])}
            for r in pie_by_cost
        ],
    }


# -- Data access (per-project dashboard) ---------------------------------------

def _resolve_project(project_ref: str) -> dict | None:
    """Resolve /project/<ref> -- accepts a numeric projects.id or the literal
    'unlinked' shortcut for the synthetic Unlinked Sessions project."""
    if project_ref == "unlinked":
        rows = run_query("SELECT id, number, slug, title, is_unlinked FROM projects WHERE is_unlinked = TRUE LIMIT 1")
    else:
        try:
            pid = int(project_ref)
        except ValueError:
            return None
        rows = run_query(f"SELECT id, number, slug, title, is_unlinked FROM projects WHERE id = {pid}")
    if not rows:
        return None
    r = rows[0]
    return {
        "id": _num(r["id"], int),
        "number": _opt(r["number"]),
        "slug": r["slug"],
        "title": r["title"],
        "is_unlinked": r["is_unlinked"] == "t",
    }


def fetch_project(project_id: int, is_unlinked: bool) -> dict:
    eff = _effective_project_id_expr()
    project_filter = (
        f"{eff} = {project_id}" if not is_unlinked else f"{eff} = {project_id}"
    )

    sessions = run_query(
        f"""SELECT ri.session_id, ri.started_at::text AS started_at, ri.issue_repo, ri.issue_number,
                   ri.branch, ri.model, ri.cost_usd, ri.premium_requests,
                   ri.tokens_input, ri.tokens_output, ri.tokens_cache_read, ri.tokens_reasoning
            FROM pipeline_run_items ri
            JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter}
            ORDER BY ri.started_at ASC NULLS LAST"""
    )

    totals_row = run_query(
        f"""SELECT COUNT(*) AS sessions,
                   COALESCE(SUM(ri.premium_requests),0) AS premium_requests,
                   COUNT(ri.premium_requests) AS items_with_premium,
                   COALESCE(SUM(ri.cost_usd),0) AS cost_usd,
                   COUNT(ri.cost_usd) AS items_with_cost,
                   COUNT(DISTINCT (ri.issue_repo || '#' || ri.issue_number))
                       FILTER (WHERE ri.issue_number IS NOT NULL) AS issues,
                   COALESCE(SUM(ri.tokens_input),0) + COALESCE(SUM(ri.tokens_output),0) AS total_tokens,
                   COUNT(*) FILTER (WHERE ri.tokens_input IS NOT NULL OR ri.tokens_output IS NOT NULL) AS items_with_tokens
            FROM pipeline_run_items ri
            JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter}"""
    )[0]

    def period_rows(trunc: str, fmt: str):
        return run_query(
            f"""SELECT to_char(date_trunc('{trunc}', ri.started_at), '{fmt}') AS period,
                       COALESCE(SUM(ri.cost_usd),0) AS cost,
                       COUNT(*) AS sessions,
                       COALESCE(SUM(ri.tokens_input),0) + COALESCE(SUM(ri.tokens_output),0) AS total_tokens,
                       COUNT(*) FILTER (WHERE ri.tokens_input IS NOT NULL OR ri.tokens_output IS NOT NULL) AS token_sessions
                FROM pipeline_run_items ri
                JOIN pipeline_runs pr ON pr.id = ri.run_id
                WHERE {project_filter} AND ri.started_at IS NOT NULL
                GROUP BY 1 ORDER BY 1"""
        )

    monthly = period_rows("month", "YYYY-MM")
    weekly = period_rows("week", 'IYYY-"W"IW')
    yearly = period_rows("year", "YYYY")

    model_rows = run_query(
        f"""SELECT ri.model,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(ri.api_requests),0) AS requests_count,
                   COALESCE(SUM(ri.premium_requests),0) AS requests_premium,
                   COALESCE(SUM(ri.tokens_input),0) AS input_tokens,
                   COALESCE(SUM(ri.tokens_output),0) + COALESCE(SUM(ri.tokens_input),0) AS total_tokens,
                   COALESCE(SUM(ri.tokens_cache_read),0) AS cache_read_tokens
            FROM pipeline_run_items ri
            JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.model IS NOT NULL
            GROUP BY ri.model
            ORDER BY sessions DESC"""
    )

    pie_sessions_rows = run_query(
        f"""SELECT ri.model, COUNT(*) AS value FROM pipeline_run_items ri
            JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.model IS NOT NULL
            GROUP BY ri.model ORDER BY value DESC"""
    )
    pie_issues_rows = run_query(
        f"""SELECT ri.model, COUNT(DISTINCT (ri.issue_repo || '#' || ri.issue_number)) AS value
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.model IS NOT NULL AND ri.issue_number IS NOT NULL
            GROUP BY ri.model ORDER BY value DESC"""
    )
    pie_cost_rows = run_query(
        f"""SELECT ri.model, COALESCE(SUM(ri.cost_usd),0) AS value
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.model IS NOT NULL
            GROUP BY ri.model HAVING COALESCE(SUM(ri.cost_usd),0) > 0
            ORDER BY value DESC"""
    )

    top_issues_premium = run_query(
        f"""SELECT ri.issue_repo, ri.issue_number,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(ri.premium_requests),0) AS premium_requests,
                   COUNT(ri.premium_requests) AS items_with_premium,
                   COALESCE(SUM(ri.cost_usd),0) AS cost_usd,
                   COUNT(ri.cost_usd) AS items_with_cost,
                   MODE() WITHIN GROUP (ORDER BY ri.model) AS top_model
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.issue_number IS NOT NULL
            GROUP BY ri.issue_repo, ri.issue_number
            ORDER BY premium_requests DESC NULLS LAST
            LIMIT 10"""
    )
    top_issues_cost = run_query(
        f"""SELECT ri.issue_repo, ri.issue_number,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(ri.premium_requests),0) AS premium_requests,
                   COUNT(ri.premium_requests) AS items_with_premium,
                   COALESCE(SUM(ri.cost_usd),0) AS cost_usd,
                   COUNT(ri.cost_usd) AS items_with_cost,
                   MODE() WITHIN GROUP (ORDER BY ri.model) AS top_model
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.issue_number IS NOT NULL
            GROUP BY ri.issue_repo, ri.issue_number
            ORDER BY cost_usd DESC NULLS LAST
            LIMIT 10"""
    )

    by_repo_branch = run_query(
        f"""SELECT ri.issue_repo, COALESCE(ri.branch, '—') AS branch,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(ri.premium_requests),0) AS premium_requests,
                   COUNT(ri.premium_requests) AS items_with_premium,
                   COALESCE(SUM(ri.cost_usd),0) AS cost_usd,
                   COUNT(ri.cost_usd) AS items_with_cost,
                   MODE() WITHIN GROUP (ORDER BY ri.model) AS top_model
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.issue_repo IS NOT NULL
            GROUP BY ri.issue_repo, ri.branch
            ORDER BY cost_usd DESC NULLS LAST, sessions DESC
            LIMIT 20"""
    )

    heatmap_daily = run_query(
        f"""SELECT date_trunc('day', ri.started_at)::date::text AS day, COUNT(*) AS sessions
            FROM pipeline_run_items ri JOIN pipeline_runs pr ON pr.id = ri.run_id
            WHERE {project_filter} AND ri.started_at >= now() - INTERVAL '91 days'
            GROUP BY 1"""
    )

    def _period_out(rows):
        return [
            {
                "period": r["period"],
                "cost": _num(r["cost"]),
                "sessions": _num(r["sessions"], int),
                "total_tokens": _num(r["total_tokens"], int),
                "token_sessions": _num(r["token_sessions"], int),
            }
            for r in rows
        ]

    def _issue_row_out(r):
        return {
            "issue_repo": r["issue_repo"],
            "issue_number": _num(r["issue_number"], int),
            "sessions": _num(r["sessions"], int),
            "premium_requests": _num(r["premium_requests"], int) if _num(r["items_with_premium"], int) else None,
            "cost_usd": _num(r["cost_usd"]) if _num(r["items_with_cost"], int) else None,
            "top_model": _opt(r["top_model"]),
        }

    def _repo_row_out(r):
        return {
            "issue_repo": r["issue_repo"],
            "branch": r["branch"],
            "sessions": _num(r["sessions"], int),
            "premium_requests": _num(r["premium_requests"], int) if _num(r["items_with_premium"], int) else None,
            "cost_usd": _num(r["cost_usd"]) if _num(r["items_with_cost"], int) else None,
            "top_model": _opt(r["top_model"]),
        }

    sessions_out = []
    for s in sessions:
        cost = _num(s["cost_usd"]) if s["cost_usd"] not in (None, "") else None
        tokens_in = _num(s["tokens_input"], int) if s["tokens_input"] not in (None, "") else None
        tokens_out = _num(s["tokens_output"], int) if s["tokens_output"] not in (None, "") else None
        total_tok = (tokens_in or 0) + (tokens_out or 0) if (tokens_in is not None or tokens_out is not None) else None
        sessions_out.append({
            "id": s["session_id"] or "—",
            "date": (s["started_at"] or "")[:10] or None,
            "repo": s["issue_repo"] or "—",
            "branch": s["branch"] or "—",
            "premium": _num(s["premium_requests"], int) if s["premium_requests"] not in (None, "") else None,
            "cost": cost,
            "conf": "exact" if cost is not None else "unknown",
            "issues": [f"#{_num(s['issue_number'], int)}"] if s["issue_number"] not in (None, "") else [],
            "top_models": [{"name": model_display_name(s["model"]), "share": 100}] if s["model"] else [],
            "tokens": total_tok,
        })

    model_data_out = []
    for m in model_rows:
        model_data_out.append({
            "model_id": m["model"],
            "display_name": model_display_name(m["model"]),
            "sessions": _num(m["sessions"], int),
            "requests_count": _num(m["requests_count"], int),
            "requests_premium": _num(m["requests_premium"], int),
            "input_tokens": _num(m["input_tokens"], int),
            "total_tokens": _num(m["total_tokens"], int),
            "cache_read_tokens": _num(m["cache_read_tokens"], int),
        })

    def _pie_out(rows, is_cost=False):
        out = []
        for r in rows:
            v = round(_num(r["value"]), 4) if is_cost else _num(r["value"], int)
            out.append({"label": model_display_name(r["model"]), "value": v, "color": model_color(r["model"])})
        return out

    heatmap_by_day = {r["day"]: _num(r["sessions"], int) for r in heatmap_daily}

    items_with_cost = _num(totals_row["items_with_cost"], int)
    items_with_premium = _num(totals_row["items_with_premium"], int)
    items_with_tokens = _num(totals_row["items_with_tokens"], int)
    total_sessions = _num(totals_row["sessions"], int)

    return {
        "summary": {
            "sessions": total_sessions,
            "premium_requests": _num(totals_row["premium_requests"], int) if items_with_premium else None,
            "cost_usd": _num(totals_row["cost_usd"]) if items_with_cost else None,
            "items_with_cost": items_with_cost,
            "issues": _num(totals_row["issues"], int),
            "total_tokens": _num(totals_row["total_tokens"], int),
            "items_with_tokens": items_with_tokens,
        },
        "heatmap_by_day": heatmap_by_day,
        "monthly": _period_out(monthly),
        "weekly": _period_out(weekly),
        "yearly": _period_out(yearly),
        "model_data": model_data_out,
        "pie_sessions": _pie_out(pie_sessions_rows),
        "pie_issues": _pie_out(pie_issues_rows),
        "pie_cost": _pie_out(pie_cost_rows, is_cost=True),
        "top_issues_premium": [_issue_row_out(r) for r in top_issues_premium],
        "top_issues_cost": [_issue_row_out(r) for r in top_issues_cost],
        "by_repo_branch": [_repo_row_out(r) for r in by_repo_branch],
        "sessions": sessions_out,
    }

# -- HTML rendering: shared helpers ---------------------------------------------

EMDASH = "\u2014"

def _page(title: str, css: str, body_and_script: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
{body_and_script}
</html>"""


def _stat_div(value: str, label: str, count: int | None = None) -> str:
    attr = f' data-count="{count}"' if count is not None else ""
    return f'<div class="stat"><div class="stat-value"{attr}>{html.escape(value)}</div><div class="stat-label">{html.escape(label)}</div></div>'


def _bar_cell(value: float | None, max_value: float, fmt: str) -> str:
    """The reference's inline mini-bar-chart table cell: a fractional-width
    fill bar plus a numeric label, or an empty '—' bar when value is None."""
    if value is None:
        return '<div class="bar-wrap"><div class="bar-fill" style="width:0%"></div><span class="bar-label">—</span></div>'
    pct = round(value / max_value * 100) if max_value > 0 else 0
    return f'<div class="bar-wrap"><div class="bar-fill" style="width:{pct}%"></div><span class="bar-label">{html.escape(fmt.format(value))}</span></div>'


def _conf_cell(conf: str) -> str:
    return f'<td class="conf conf-{conf}"><span class="dot dot-{conf}"></span>{conf}</td>'


def _model_cell(model_name: str | None, share: int = 100) -> str:
    if not model_name:
        return '<td class="model-cell">—</td>'
    return f'<td class="model-cell"><span class="model-tag">{html.escape(model_name)}</span><span class="model-share">{share}%</span></td>'


# -- Index (Projects Overview) page ---------------------------------------------

def render_index(data: dict) -> str:
    projects = data["projects"]
    total_sessions = sum(p["sessions"] for p in projects)
    total_cost = sum(p["cost_usd"] for p in projects)
    any_cost = any(p["items_with_cost"] for p in projects)
    cost_display = f"${total_cost:.2f}" if any_cost else "no data"

    meta_line = f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &nbsp;\u00b7&nbsp; {len(projects)} project(s)"

    stats_block = f"""<div class="stats">
  {_stat_div(str(len(projects)), "Projects", len(projects))}
  {_stat_div(str(total_sessions), "Total Sessions", total_sessions)}
  {_stat_div(cost_display, "Total Est. Cost")}
</div>"""

    cards = []
    for p in projects:
        cls = "proj-card proj-card-unlinked" if p["is_unlinked"] else "proj-card"
        href = "/project/unlinked" if p["is_unlinked"] else f"/project/{p['id']}"
        num_label = "\u2205" if p["is_unlinked"] else f"#{p['number'] if p['number'] is not None else p['id']}"
        cost_val = f"${p['cost_usd']:.2f}" if p["items_with_cost"] else EMDASH
        premium_val = str(p["premium_requests"]) if p["items_with_premium"] else EMDASH
        issues_val = str(p["issues"]) if p["issues"] else EMDASH
        cards.append(f"""<a class="{cls}" href="{href}">
    <div class="proj-num">{num_label}</div>
    <div class="proj-title">{html.escape(p['title'])}</div>
    <div class="proj-stats">
      <div class="proj-stat"><span class="proj-val">{html.escape(cost_val)}</span><span class="proj-key">Est. Cost</span></div>
      <div class="proj-stat"><span class="proj-val" data-count="{p['sessions']}">{p['sessions']}</span><span class="proj-key">Sessions</span></div>
      <div class="proj-stat"><span class="proj-val">{html.escape(premium_val)}</span><span class="proj-key">Premium Req</span></div>
      <div class="proj-stat"><span class="proj-val">{html.escape(issues_val)}</span><span class="proj-key">Issues</span></div>
    </div>
  </a>""")
    cards_html = "\n  ".join(cards) or '<p class="empty">No projects recorded yet.</p>'

    body_html = (
        INDEX_BODY_TMPL
        .replace("__META_LINE__", meta_line)
        .replace("__STATS_BLOCK__", stats_block)
        .replace("__PROJECT_CARDS__", cards_html)
    )

    compare_data = [
        {
            "period": ("\u2205" if p["is_unlinked"] else f"#{p['number'] if p['number'] is not None else p['id']}"),
            "label": p["title"],
            "cost": round(p["cost_usd"], 4),
            "sessions": p["sessions"],
            "unlinked": p["is_unlinked"],
        }
        for p in projects
    ]

    js = (
        INDEX_JS
        .replace("__DATA_JSON__", json.dumps(compare_data))
        .replace("__IDX_PIE_BY_PROJECT_JSON__", json.dumps(data["pie_by_project"]))
        .replace("__IDX_PIE_BY_COST_JSON__", json.dumps(data["pie_by_cost"]))
        .replace("__MONTHLY_DATA_JSON__", json.dumps([
            {"period": m["period"], "cost": round(m["cost"], 4), "sessions": m["sessions"], "unlinked_cost": round(m["unlinked_cost"], 4)}
            for m in data["monthly"]
        ]))
    )

    return _page("Copilot Cost \u2014 Projects Overview", INDEX_CSS, f"<body>\n{body_html}\n{js}\n</body>")


# -- Per-project (Cost Dashboard) page -------------------------------------------

_HEATMAP_WEEKS = 13
_HEATMAP_DAYS = _HEATMAP_WEEKS * 7
_HM_CELL = 13  # px pitch (11px cell + 2px gap)
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _render_heatmap_svg(heatmap_by_day: dict) -> str:
    today = date.today()
    start = today - timedelta(days=_HEATMAP_DAYS - 1)
    # align the first column to the Monday of start's week so weeks form clean 7-day columns
    start -= timedelta(days=start.weekday())
    days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
    counts = [heatmap_by_day.get(d.isoformat(), 0) for d in days]
    max_c = max(counts) if counts else 0

    def level(c: int) -> int:
        if c == 0 or max_c == 0:
            return 0
        frac = c / max_c
        return 3 if frac > 0.66 else 2 if frac > 0.33 else 1

    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    width = len(weeks) * _HM_CELL
    height = 18 + 7 * _HM_CELL

    parts = [f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">']
    last_month = None
    for wi, week in enumerate(weeks):
        month = week[0].strftime("%b")
        if month != last_month:
            parts.append(f'<text x="{wi * _HM_CELL}" y="12" class="hm-month-label" font-size="10">{month}</text>')
            last_month = month
        for di, d in enumerate(week):
            c = heatmap_by_day.get(d.isoformat(), 0)
            lv = level(c)
            x = wi * _HM_CELL
            y = 18 + di * _HM_CELL
            label = "1 session" if c == 1 else f"{c} sessions"
            parts.append(
                f'<rect x="{x}" y="{y}" width="11" height="11" rx="2" class="hm-{lv}">'
                f'<title>{d.isoformat()}: {label}</title></rect>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_project(project: dict, data: dict) -> str:
    s = data["summary"]
    title = "\u2205 Unlinked Sessions" if project["is_unlinked"] else f"{html.escape(project['title'])} (#{project['number'] if project['number'] is not None else project['id']})"
    meta_line = f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} &nbsp;\u00b7&nbsp; {s['sessions']} session(s) loaded"

    cost_display = f"${s['cost_usd']:.4f}" if s["cost_usd"] is not None else "no data"
    premium_display = str(s["premium_requests"]) if s["premium_requests"] is not None else EMDASH
    tokens_display = fmt_tokens(s["total_tokens"]) if s["total_tokens"] else EMDASH
    metrics_display = f"{s['items_with_tokens']}/{s['sessions']}" if s["sessions"] else "0/0"

    stats_block = f"""<div class="stats">
  {_stat_div(str(s['sessions']), "Sessions", s['sessions'])}
  {_stat_div(premium_display, "Premium Requests", s['premium_requests'] if s['premium_requests'] is not None else None)}
  {_stat_div(cost_display, "Est. Cost (USD)")}
  {_stat_div(str(s['issues']), "Issues Tracked", s['issues'])}
  {_stat_div(tokens_display, "Tokens (in+out)")}
  {_stat_div(metrics_display, "Sessions w/ Metrics")}
</div>"""

    heatmap_svg = _render_heatmap_svg(data["heatmap_by_day"])

    def issue_rows(rows):
        if not rows:
            return '<tr><td colspan="6" class="empty">No issues recorded yet.</td></tr>'
        max_prem = max((r["premium_requests"] or 0) for r in rows) or 1
        max_cost = max((r["cost_usd"] or 0) for r in rows) or 1
        out = []
        for r in rows:
            out.append(
                f'<tr><td>#{r["issue_number"]}</td>'
                f'<td class="num">{r["sessions"]}</td>'
                f'<td class="num">{r["premium_requests"] if r["premium_requests"] is not None else EMDASH}</td>'
                f'<td>{_bar_cell(r["cost_usd"], max_cost, "${:.4f}")}</td>'
                f'{_conf_cell("exact" if r["cost_usd"] is not None else "unknown")}'
                f'{_model_cell(model_display_name(r["top_model"]) if r["top_model"] else None)}</tr>'
            )
        return "\n".join(out)

    def repo_rows(rows):
        if not rows:
            return '<tr><td colspan="6" class="empty">No sessions recorded yet.</td></tr>'
        max_cost = max((r["cost_usd"] or 0) for r in rows) or 1
        out = []
        for r in rows:
            out.append(
                f'<tr><td>{html.escape(r["issue_repo"])}</td>'
                f'<td class="mono">{html.escape(r["branch"])}</td>'
                f'<td class="num">{r["sessions"]}</td>'
                f'<td class="num">{r["premium_requests"] if r["premium_requests"] is not None else EMDASH}</td>'
                f'<td>{_bar_cell(r["cost_usd"], max_cost, "${:.4f}")}</td>'
                f'{_model_cell(model_display_name(r["top_model"]) if r["top_model"] else None)}</tr>'
            )
        return "\n".join(out)

    timeline_heading = f"Session Timeline \u2014 {s['sessions']} sessions \u00b7 only sessions with known cost shown"

    body_html = (
        PROJECT_BODY_TMPL
        .replace("__TITLE__", title)
        .replace("__META_LINE__", meta_line)
        .replace("__STATS_BLOCK__", stats_block)
        .replace("__HEATMAP_SVG__", heatmap_svg)
        .replace("__TOP_ISSUES_PREMIUM_ROWS__", issue_rows(data["top_issues_premium"]))
        .replace("__TOP_ISSUES_COST_ROWS__", issue_rows(data["top_issues_cost"]))
        .replace("__REPO_BRANCH_ROWS__", repo_rows(data["by_repo_branch"]))
        .replace("__TIMELINE_HEADING__", timeline_heading)
    )

    js = (
        PROJECT_JS
        .replace("__SESSIONS_JSON__", json.dumps(data["sessions"]))
        .replace("__MONTHLY_DATA_JSON__", json.dumps(data["monthly"]))
        .replace("__WEEKLY_DATA_JSON__", json.dumps(data["weekly"]))
        .replace("__YEARLY_DATA_JSON__", json.dumps(data["yearly"]))
        .replace("__MODEL_DATA_JSON__", json.dumps(data["model_data"]))
        .replace("__PIE_SESSIONS_JSON__", json.dumps(data["pie_sessions"]))
        .replace("__PIE_ISSUES_JSON__", json.dumps(data["pie_issues"]))
        .replace("__PIE_COST_JSON__", json.dumps(data["pie_cost"]))
    )

    page_title = "Unlinked Sessions" if project["is_unlinked"] else f"{project['title']} \u2014 Copilot Cost"
    return _page(page_title, PROJECT_CSS, f"<body>\n{body_html}\n{js}\n</body>")


def render_error(status: int, message: str) -> str:
    return _page(f"Error {status}", INDEX_CSS, f'<body><div class="error-box">{html.escape(message)}</div></body>')


# -- HTTP handler ----------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "gru-analytics-web/2.0"

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

        try:
            if path == "/healthz":
                self._send_html(200, "ok")
                return

            if path == "/":
                data = fetch_index()
                self._send_html(200, render_index(data))
                return

            m = re.match(r"^/project/([^/]+)$", path)
            if m:
                ref = m.group(1)
                if ref != "unlinked":
                    try:
                        _validate_ident(ref)
                        int(ref)
                    except ValueError:
                        self._send_html(400, render_error(400, f"Invalid project reference: {ref!r}"))
                        return
                project = _resolve_project(ref)
                if project is None:
                    self._send_html(404, render_error(404, f"Project {ref!r} not found"))
                    return
                data = fetch_project(project["id"], project["is_unlinked"])
                self._send_html(200, render_project(project, data))
                return

            self._send_html(404, render_error(404, "Not found"))
        except QueryError as exc:
            self._send_html(503, render_error(503, f"Analytics database unavailable: {exc}"))
        except Exception as exc:  # last-resort guard -- never let the server crash on a bad request
            self._send_html(500, render_error(500, f"Internal error: {exc}"))


def main():
    server = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    print(f"[gru-analytics-web] listening on :{WEB_PORT} (db={PG_DB} user={PG_USER})")
    server.serve_forever()


if __name__ == "__main__":
    main()
