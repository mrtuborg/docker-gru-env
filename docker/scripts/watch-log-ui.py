#!/usr/bin/env python3
"""
watch-log-ui.py — Watcher dashboard: pipeline board + live log stream.

Usage:
    python3 watch-log-ui.py <container> <port> [--config PATH]

Serves:
    GET /            HTML dashboard (pipeline columns + live log)
    GET /api/board   JSON: GitHub project board state (polled every 30 s)
    GET /api/logs    SSE:  docker logs -f stream
    GET /api/status  JSON: container running/stopped + current issue
    GET /api/devices JSON: device status (read from device_status_file if configured)
"""

import calendar
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path


# ---------------------------------------------------------------------------
# Config loader (minimal YAML — no pyyaml dependency needed on host)
# ---------------------------------------------------------------------------

def _yaml_scalar(text, key):
    """Extract a simple scalar value from YAML text."""
    m = re.search(rf'^\s*{re.escape(key)}\s*:\s*["\']?([^\n"\'#]+?)["\']?\s*(?:#[^\n]*)?\s*$',
                  text, re.MULTILINE)
    return m.group(1).strip() if m else None

def _yaml_int(text, key):
    v = _yaml_scalar(text, key)
    return int(v) if v and v.isdigit() else None

def _yaml_list(text, key):
    """Extract a flow-sequence like [A, B, C] from YAML."""
    m = re.search(rf'^\s*{re.escape(key)}\s*:\s*\[([^\]]+)\]', text, re.MULTILINE)
    if not m:
        return []
    return [s.strip().strip('"\'') for s in m.group(1).split(',')]

def load_config(path):
    """Return a dict with keys: gh_host, repo, project_owner, project_number,
    project_name, stage_order."""
    cfg = {}
    if path and Path(path).exists():
        txt = Path(path).read_text()
        cfg['gh_host']         = _yaml_scalar(txt, 'gh_host') or 'github.com'
        cfg['repo']            = _yaml_scalar(txt, 'data_repo') or ''
        cfg['project_owner']   = _yaml_scalar(txt, 'owner') or (cfg['repo'].split('/')[0] if '/' in cfg['repo'] else '')
        cfg['project_number']  = _yaml_int(txt, 'number') or 0
        cfg['project_name']    = _yaml_scalar(txt, 'name') or 'Watcher Board'
        cfg['stage_order']     = _yaml_list(txt, 'stage_order') or ['Todo', 'In Progress', 'Done']
        cfg['device_status_file'] = _yaml_scalar(txt, 'device_status_file') or ''
    return cfg


# ---------------------------------------------------------------------------
# Board cache — polls GitHub project via gh CLI every 30 s
# ---------------------------------------------------------------------------

GQL_BOARD = """
query GetBoard($org: String!, $num: Int!) {
  organization(login: $org) {
    projectV2(number: $num) {
      title
      items(first: 100) {
        nodes {
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number title url state body
              labels(first: 5) { nodes { name color } }
              subIssues { totalCount }
            }
          }
        }
      }
    }
  }
  user(login: $org) {
    projectV2(number: $num) {
      title
      items(first: 100) {
        nodes {
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number title url state body
              labels(first: 5) { nodes { name color } }
              subIssues { totalCount }
            }
          }
        }
      }
    }
  }
}
"""

class BoardCache:
    def __init__(self, config):
        self.config = config
        self._data  = {}
        self._error = None
        self._lock  = threading.Lock()
        self._stop  = threading.Event()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _fetch(self):
        cfg = self.config
        if not cfg.get('project_owner') or not cfg.get('project_number'):
            return {'error': 'No project configured'}
        env = dict(os.environ)
        if cfg.get('gh_host'):
            env['GH_HOST'] = cfg['gh_host']
        try:
            result = subprocess.run(
                ['gh', 'api', 'graphql',
                 '-F', f'org={cfg["project_owner"]}',
                 '-F', f'num={cfg["project_number"]}',
                 '-f', f'query={GQL_BOARD}'],
                capture_output=True, text=True, timeout=20, env=env
            )
            # gh exits non-zero when one alias (organization vs user) resolves
            # but the other doesn't — try to parse JSON regardless of exit code.
            try:
                parsed = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                return {'error': result.stderr.strip() or 'gh api failed'}
            # If no data at all, surface the gh error
            if not parsed.get('data'):
                return {'error': result.stderr.strip() or parsed.get('errors', [{}])[0].get('message', 'gh api failed')}
            return parsed
        except Exception as e:
            return {'error': str(e)}

    def _loop(self):
        while not self._stop.is_set():
            data = self._fetch()
            with self._lock:
                self._data  = data
                self._error = data.get('error')
            self._stop.wait(30)

    def get(self):
        with self._lock:
            return dict(self._data), self._error

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# Log buffer — tails docker logs and parses current-issue state
# ---------------------------------------------------------------------------

class LogBuffer:
    ISSUE_START = re.compile(r'Starting session for issue #(\d+)\s+\[([^\]]+)\]')
    ISSUE_DONE  = re.compile(r'[✓✗] Issue #(\d+) (completed|failed)')
    # ⏸  No actionable issues found — polling again in 300s  (14:32:05)
    SLEEP_START = re.compile(r'polling again in (\d+)s\s+\((\d{2}:\d{2}:\d{2})\)')
    # Lines the watcher itself emits — used to build the key-events feed.
    # Everything else is copilot session output (too noisy to show).
    SIGNIFICANT = re.compile(
        r'Starting session|'
        r'[✓✗] Issue #|'
        r'⏸\s+No actionable|'
        r'Will re-poll|'
        r'Skipped \(retry|'
        r'All actionable|'
        r'Running.*cost|'        # "Running cost-sync…" / "Running best-effort…"
        r'ERROR|WARN|'
        r'cost-board-sync|'
        r'Using stage-prompts dir:|'  # watcher log line — NOT agent bash commands
        r'Pilot gate|'
        r'unreachable|'
        r'hil-update exit|'
        r'hil-check exit|'
        r'hil-stress exit'
    )

    # Timestamp in session start/done lines: "(Mon Jun 15 08:01:43 UTC 2026)"
    LOG_TS = re.compile(r'\((\w{3} \w{3}\s+\d+ \d{2}:\d{2}:\d{2} \w+ \d{4})\)')

    def __init__(self, container):
        self.container    = container
        self._lines       = deque(maxlen=2000)
        self._events      = deque(maxlen=100)  # significant watcher lines only
        self._run_start   = time.time()         # ignore tail-replayed lines older than this
        self._listeners   = []
        self._lock        = threading.Lock()
        self._active      = None   # {'num': N, 'stage': S, 'started': ts}
        self._done        = []     # list of {'num', 'stage', 'status', 'duration_s'}
        self._sleep_until = None  # Unix timestamp of next board scan (float)
        self._backfill_from_logs(container)
        t = threading.Thread(target=self._stream, daemon=True)
        t.start()

    def _parse_log_ts(self, line):
        """Extract epoch from a '(Mon Jun 15 08:01:43 UTC 2026)' timestamp in a log line."""
        m = self.LOG_TS.search(line)
        if not m:
            return None
        try:
            import email.utils
            # strptime can't handle UTC tz name directly; replace with +0000
            ts_str = m.group(1).replace(' UTC ', ' +0000 ')
            import time as _t
            return _t.mktime(_t.strptime(ts_str, '%a %b %d %H:%M:%S +0000 %Y')) - _t.timezone
        except Exception:
            return None

    def _backfill_from_logs(self, container):
        """Parse /logs/run-*.log inside the container to hydrate _active with any
        in-progress issue. Completed issues are NOT backfilled into _done — the
        dashboard section is titled "Completed This Run" and must only show
        completions from the current watcher session."""
        try:
            result = subprocess.run(
                ['docker', 'exec', container, 'sh', '-c',
                 'ls /logs/run-*.log 2>/dev/null | sort'],
                capture_output=True, text=True, timeout=5
            )
            log_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
        except Exception:
            return

        pending = {}   # num -> {stage, started_epoch}

        for log_file in log_files:
            if not log_file:
                continue
            try:
                cat = subprocess.run(
                    ['docker', 'exec', container, 'cat', log_file],
                    capture_output=True, text=True, timeout=10
                )
                for line in cat.stdout.splitlines():
                    ms = self.ISSUE_START.search(line)
                    if ms:
                        num, stage = int(ms.group(1)), ms.group(2)
                        epoch = self._parse_log_ts(line)
                        pending[num] = {'stage': stage, 'started_epoch': epoch}
                    md = self.ISSUE_DONE.search(line)
                    if md:
                        num = int(md.group(1))
                        pending.pop(num, None)
            except Exception:
                continue

        # Restore _active for any issue that started but never completed
        # (e.g. dashboard restarted mid-run)
        if pending:
            num, info = next(reversed(pending.items()))
            with self._lock:
                if self._active is None:
                    self._active = {
                        'num': num,
                        'stage': info.get('stage', ''),
                        'started': '',
                        'started_epoch': info.get('started_epoch', time.time()),
                    }

    def _stream(self):
        while True:
            try:
                proc = subprocess.Popen(
                    ['docker', 'logs', '-f', '--tail', '200', self.container],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in proc.stdout:
                    line = line.rstrip('\n')
                    self._ingest(line)
                proc.wait()
            except Exception:
                pass
            time.sleep(3)   # retry after container restarts

    def _ingest(self, line):
        m = self.ISSUE_START.search(line)
        if m:
            with self._lock:
                self._active = {'num': int(m.group(1)), 'stage': m.group(2),
                                'started': time.strftime('%H:%M:%S'),
                                'started_epoch': time.time()}
                self._sleep_until = None  # woke up — clear countdown
        m = self.ISSUE_DONE.search(line)
        if m:
            with self._lock:
                entry = {'num': int(m.group(1)), 'status': m.group(2)}
                if self._active and self._active['num'] == entry['num']:
                    entry['stage'] = self._active['stage']
                    entry['duration_s'] = int(time.time() - self._active.get('started_epoch', time.time()))
                    self._active = None
                self._done.append(entry)
                if len(self._done) > 50:
                    self._done.pop(0)
        m = self.SLEEP_START.search(line)
        if m:
            secs = int(m.group(1))
            hh, mm, ss = map(int, m.group(2).split(':'))
            # The container runs UTC; reconstruct the timestamp in UTC to avoid
            # a timezone offset (e.g. CEST = UTC+2 would place the result 2h in
            # the past, causing the server to discard the countdown immediately).
            now_utc = time.gmtime()
            start = calendar.timegm((now_utc.tm_year, now_utc.tm_mon, now_utc.tm_mday,
                                     hh, mm, ss, 0, 0, 0))
            sleep_until = start + secs
            if sleep_until > time.time() + secs + 60:
                sleep_until -= 86400
            with self._lock:
                self._sleep_until = sleep_until

        with self._lock:
            self._lines.append(line)
            # Skip tail-replayed lines (arrive within first 5s of startup)
            # so stale events from prior runs don't pollute Session Events.
            if self.SIGNIFICANT.search(line) and time.time() > self._run_start + 5:
                is_sleep = bool(re.search(r'⏸\s+No actionable|Will re-poll', line))
                last_is_sleep = bool(
                    self._events and re.search(r'⏸\s+No actionable|Will re-poll', self._events[-1]['line'])
                )
                if not (is_sleep and last_is_sleep):
                    self._events.append({'t': time.strftime('%H:%M:%S'), 'line': line})
            listeners = list(self._listeners)
        for q in listeners:
            q.append(line)

    def tail(self, n=200):
        with self._lock:
            return list(self._lines)[-n:]

    def subscribe(self):
        """Returns a deque; append() is called for each new line."""
        q = deque(maxlen=500)
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def status(self):
        with self._lock:
            # Discard sleep_until if it's more than 60s in the past — it's
            # a stale value replayed from the log tail of a prior run.
            su = self._sleep_until
            if su is not None and su < time.time() - 60:
                self._sleep_until = None
                su = None
            return {
                'active':      self._active,
                'done':        list(self._done[-10:]),
                'sleep_until': su,
                'events':      list(self._events)[-30:],
            }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    board:  BoardCache
    logs:   LogBuffer
    config: dict

    def log_message(self, fmt, *args):
        pass  # suppress access log noise

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            self._html()
        elif path == '/api/board':
            self._board()
        elif path == '/api/devices':
            self._devices()
        elif path == '/api/logs':
            self._logs_sse()
        elif path == '/api/status':
            self._status()
        else:
            self.send_response(404); self.end_headers()

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _board(self):
        data, err = self.board.get()
        if err:
            self._json({'error': err, 'items': []})
            return
        items = []
        try:
            # Projects can be owned by an org or by a user — probe both.
            gdata = data.get('data', {})
            project = (
                (gdata.get('organization') or {}).get('projectV2') or
                (gdata.get('user') or {}).get('projectV2')
            )
            if project is None:
                self._json({'error': 'Project not found (check project_owner and project_number in config)', 'items': []})
                return
            nodes = project['items']['nodes']
            for n in nodes:
                c = n.get('content') or {}
                if not c.get('number'):
                    continue
                status = (n.get('fieldValueByName') or {}).get('name', 'Unknown')
                items.append({
                    'number':    c['number'],
                    'title':     c.get('title', ''),
                    'url':       c.get('url', ''),
                    'state':     c.get('state', ''),
                    'status':    status,
                    'body':      (c.get('body') or '')[:2000],
                    'labels':    [l['name'] for l in (c.get('labels') or {}).get('nodes', [])],
                    'is_parent': (c.get('subIssues') or {}).get('totalCount', 0) > 0,
                })
        except (KeyError, TypeError) as e:
            self._json({'error': str(e), 'items': [], 'raw': data})
            return
        self._json({'items': items, 'stage_order': self.config.get('stage_order', []),
                    'project_name': self.config.get('project_name', '')})

    def _status(self):
        log_status = self.logs.status()
        # Check if container is running
        r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}',
                            self.logs.container],
                           capture_output=True, text=True)
        running = r.stdout.strip() == 'true'
        self._json({'container': self.logs.container, 'running': running,
                    **log_status})

    def _devices(self):
        path = getattr(self, 'device_status_path', None)
        if not path or not Path(path).exists():
            self._json({'devices': []})
            return
        try:
            data = json.loads(Path(path).read_text())
            self._json(data)
        except Exception as e:
            self._json({'devices': [], 'error': str(e)})

    def _logs_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        # Subscribe BEFORE sending tail so no lines are dropped during the flush
        q = self.logs.subscribe()
        for line in self.logs.tail(100):
            self._sse(line)
        # Stream live
        try:
            while True:
                if q:
                    line = q.popleft()
                    self._sse(line)
                else:
                    time.sleep(0.1)
                    self._sse_keepalive()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.logs.unsubscribe(q)

    def _sse(self, line):
        data = json.dumps(line)
        try:
            self.wfile.write(f'data: {data}\n\n'.encode())
            self.wfile.flush()
        except Exception:
            raise BrokenPipeError

    def _sse_keepalive(self):
        try:
            self.wfile.write(b': keepalive\n\n')
            self.wfile.flush()
        except Exception:
            raise BrokenPipeError

    def _html(self):
        html = HTML_TEMPLATE.replace('__CONTAINER__', self.logs.container)
        body = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gru's Lab — Watcher Dashboard</title>
<style>
  :root {
    --bg:       #0a0e14;
    --surface:  #111820;
    --surface2: #161d27;
    --border:   #1f2d3d;
    --border2:  #263545;
    --text:     #cdd9e5;
    --muted:    #636e7b;
    --accent:   #58a6ff;
    --accent2:  #79c0ff;
    --green:    #3fb950;
    --yellow:   #d29922;
    --orange:   #db6d28;
    --red:      #f85149;
    --purple:   #bc8cff;
    --cyan:     #39c5cf;
  }
  [data-theme="light"] {
    --bg:       #f6f8fa;
    --surface:  #ffffff;
    --surface2: #f0f2f4;
    --border:   #d0d7de;
    --border2:  #c8d0d8;
    --text:     #1f2328;
    --muted:    #656d76;
    --accent:   #0969da;
    --accent2:  #218bff;
    --green:    #1a7f37;
    --yellow:   #9a6700;
    --orange:   #bc4c00;
    --red:      #d1242f;
    --purple:   #8250df;
    --cyan:     #0969da;
  }
  [data-theme="light"] .badge-todo    { background:#ddf4ff; border-color:#54aeff; color:#0969da; }
  [data-theme="light"] .badge-check   { background:#dafbe1; border-color:#34d058; color:#1a7f37; }
  [data-theme="light"] .badge-update  { background:#fff8c5; border-color:#d4a72c; color:#9a6700; }
  [data-theme="light"] .badge-stress  { background:#ffebe9; border-color:#ff8182; color:#d1242f; }
  [data-theme="light"] .badge-log     { background:#fbefff; border-color:#d8b4fe; color:#8250df; }
  [data-theme="light"] .badge-review  { background:#ddf4ff; border-color:#54aeff; color:#0969da; }
  [data-theme="light"] .badge-done    { background:#dafbe1; border-color:#34d058; color:#1a7f37; }
  [data-theme="light"] .badge-unknown { background:#f6f8fa; border-color:#d0d7de; color:#656d76; }
  [data-theme="light"] .done-chip.ok  { background:#dafbe1; border-color:#34d058; color:#1a7f37; }
  [data-theme="light"] .done-chip.err { background:#ffebe9; border-color:#ff8182; color:#d1242f; }
  [data-theme="light"] .dot-green     { box-shadow: 0 0 6px #1a7f37; }
  .theme-btn {
    background: none; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); cursor: pointer; font-size: 15px; padding: 3px 9px;
    transition: border-color .2s, color .2s;
  }
  .theme-btn:hover { border-color: var(--accent); color: var(--accent); }
  *, *::before, *::after { box-sizing: border-box; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 14px; min-height: 100vh;
    padding: 20px 24px; max-width: 1400px; margin: 0 auto;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; font-size: .85em; }

  /* ── Divider ── */
  .divider {
    height: 1px; margin: 18px 0;
    background: linear-gradient(90deg, transparent, var(--border2), transparent);
  }

  /* ── Cards ── */
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
  }
  .card-active {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 15%, transparent);
  }

  /* ── Section labels ── */
  .section-label {
    font-size: 10px; font-weight: 600; letter-spacing: .1em;
    text-transform: uppercase; color: var(--muted); margin-bottom: 10px;
  }

  /* ── Badges ── */
  .badge {
    font-size: 10px; padding: 2px 8px; border-radius: 20px;
    font-weight: 600; display: inline-block; border: 1px solid transparent;
  }
  .badge-todo    { background:#0d2044; border-color:#1f4070; color:#79c0ff; }
  .badge-check   { background:#0d2b1a; border-color:#196130; color:#56d364; }
  .badge-update  { background:#2b1d08; border-color:#5a3b10; color:#e3b341; }
  .badge-stress  { background:#2b0d0d; border-color:#6e2020; color:#f47067; }
  .badge-log     { background:#1c0f33; border-color:#3d2070; color:#bc8cff; }
  .badge-review  { background:#0d2233; border-color:#1a4060; color:#79c0ff; }
  .badge-done    { background:#0d2b1a; border-color:#196130; color:#3fb950; }
  .badge-unknown { background:#1c1c1c; border-color:#333;    color:#636e7b; }

  /* ── Status dot ── */
  .dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; }
  .dot-green  { background:var(--green);  box-shadow:0 0 6px var(--green); }
  .dot-red    { background:var(--red);    box-shadow:0 0 6px var(--red); }
  .dot-muted  { background:var(--muted); }

  /* ── Spinner ── */
  .spinner {
    border: 2px solid var(--border2); border-top-color: var(--accent);
    border-radius: 50%; width: 13px; height: 13px;
    animation: spin .8s linear infinite; flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Pulse ── */
  .pulse { animation: pulse 2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.45} }

  /* ── Layout grid ── */
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .flex    { display: flex; }
  .flex-col { flex-direction: column; }
  .gap-2   { gap: 8px; }
  .gap-3   { gap: 12px; }
  .gap-4   { gap: 16px; }
  .items-center { align-items: center; }
  .items-baseline { align-items: baseline; }
  .justify-between { justify-content: space-between; }
  .ml-auto { margin-left: auto; }
  .flex-1  { flex: 1; }
  .flex-wrap { flex-wrap: wrap; }
  .hidden  { display: none !important; }
  .overflow-x-auto { overflow-x: auto; }
  .text-center { text-align: center; }

  /* ── Typography helpers ── */
  .text-xs   { font-size: 11px; }
  .text-sm   { font-size: 12px; }
  .text-lg   { font-size: 18px; }
  .text-2xl  { font-size: 22px; }
  .text-3xl  { font-size: 30px; }
  .font-bold { font-weight: 700; }
  .font-mono { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; }
  .italic    { font-style: italic; }
  .leading-snug { line-height: 1.35; }
  .col-accent { color: var(--accent); }
  .col-accent2{ color: var(--accent2); }
  .col-muted  { color: var(--muted); }
  .col-text   { color: var(--text); }
  .col-green  { color: var(--green); }
  .col-red    { color: var(--red); }
  .mb-1 { margin-bottom: 4px; }
  .mb-2 { margin-bottom: 8px; }
  .mb-3 { margin-bottom: 12px; }
  .mb-4 { margin-bottom: 16px; }
  .mb-5 { margin-bottom: 20px; }
  .mt-1 { margin-top: 4px; }

  /* ── Header ── */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
  }
  header .logo { font-size: 22px; margin-right: 10px; }
  header h1 { font-size: 16px; font-weight: 700; color: var(--text); line-height:1.2; }
  header .sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
  header .right { display:flex; align-items:center; gap:16px; }

  /* ── Now Processing ── */
  #now-active .now-num {
    font-size: 36px; font-weight: 700; color: var(--text);
    font-variant-numeric: tabular-nums; line-height: 1;
  }

  /* ── Queue ── */
  #queue-list { max-height: 220px; overflow-y: auto; }
  .queue-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 7px 0; border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .queue-item:last-child { border-bottom: none; }
  .queue-pos { color: var(--muted); font-size: 10px; min-width: 18px; }
  .queue-title { color: var(--text); flex: 1; margin: 0 8px;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .issue-num { color: var(--muted); font-size: 11px; }

  /* ── Pipeline ── */
  .pipeline-col { min-width: 160px; max-width: 220px; flex: 1; }
  .pipeline-col-header {
    font-size: 10px; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; color: var(--muted);
    margin-bottom: 8px; padding-bottom: 6px;
    border-bottom: 2px solid var(--border2);
  }
  .pipe-card {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 6px; padding: 9px 10px; margin-bottom: 8px;
  }
  .pipe-card.active {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent);
  }
  .pipe-title { font-size: 12px; color: var(--text); line-height: 1.35; margin-bottom: 6px; }
  .pipe-footer { display:flex; align-items:center; justify-content:space-between; }

  /* ── Done chips ── */
  .done-chip {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; padding: 3px 10px; border-radius: 20px;
    border: 1px solid var(--border2); background: var(--surface2); color: var(--muted);
  }
  .done-chip.ok  { border-color:#196130; color:var(--green); background:#0d2b1a; }
  .done-chip.ok:hover  { background:#153d22; }
  .done-chip.err { border-color:#6e2020; color:var(--red);   background:#2b0d0d; }
  .done-chip.err:hover { background:#3d1515; }
  .done-chip.running { border-color:var(--accent); color:var(--accent); background:color-mix(in srgb,var(--accent) 12%,transparent); text-decoration:none; cursor:pointer; }
  .done-chip.running:hover { background:color-mix(in srgb,var(--accent) 22%,transparent); }
  .done-chip.waiting { border-color:var(--border2); color:var(--text); background:var(--surface2); text-decoration:none; cursor:pointer; }
  .done-chip.waiting:hover { border-color:var(--accent); color:var(--accent); }

  /* ── Device chips ── */
  .dev-chip {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11px; padding: 4px 10px; border-radius: 20px;
    border: 1px solid var(--border2); background: var(--surface2); color: var(--muted);
  }
  .dev-pending  { border-color: var(--border2); color: var(--muted); }
  .dev-updating { border-color: var(--yellow); color: var(--yellow); background: #2b1d08; }
  .dev-done     { border-color: var(--green);  color: var(--green);  background: #0d2b1a; }
  .dev-failed   { border-color: var(--red);    color: var(--red);    background: #2b0d0d; }
  [data-theme="light"] .dev-updating { background: #fff8c5; color: #9a6700; }
  [data-theme="light"] .dev-done     { background: #dafbe1; color: #1a7f37; }
  [data-theme="light"] .dev-failed   { background: #ffebe9; color: #d1242f; }

  /* ── Issue details table ── */
  .detail-table { width:100%; border-collapse:collapse; font-size:12px; }
  .detail-table td { padding: 5px 0; vertical-align:top; }
  .detail-table td:first-child { color:var(--muted); width:90px; }

  /* ── Session events ── */
  .event-row { display:flex; gap:10px; padding:5px 0; border-bottom:1px solid var(--border); font-size:11px; }
  .event-row:last-child { border-bottom:none; }
  .event-time { color:var(--muted); font-family:ui-monospace,monospace; flex-shrink:0; }
  .event-line { color:var(--text); word-break:break-word; }
  .event-line.ev-ok   { color:var(--green); }
  .event-line.ev-fail { color:var(--red); }
  .event-line.ev-warn { color:var(--orange); }
  .event-line.ev-start{ color:var(--accent); }
  .event-line.ev-idle { color:var(--muted); }

  /* ── Full log ── */
  .log-toggle {
    background:none; border:none; cursor:pointer; padding:4px 0;
    display:flex; align-items:center; gap:6px;
    font-size:11px; color:var(--muted);
    transition: color .15s;
  }
  .log-toggle:hover { color:var(--text); }
  #log-panel {
    height: 220px; overflow-y: auto; padding: 4px 0;
    font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
    font-size: 11px; line-height: 1.6;
  }
  .log-line { white-space: pre-wrap; word-break: break-all; color: var(--text); }
  .log-success { color: var(--green); }
  .log-fail    { color: var(--red); }
  .log-start   { color: var(--accent); font-weight: 600; }
  .log-warn    { color: var(--orange); }
  .log-skip    { color: var(--muted); }

  /* ── Header brand ── */
  header h1 {
    font-size: 20px; font-weight: 700; color: var(--accent);
    text-shadow: 0 0 18px color-mix(in srgb, var(--accent) 40%, transparent);
    letter-spacing: -0.3px; line-height: 1.2;
  }
  [data-theme="light"] header h1 {
    text-shadow: 0 0 14px color-mix(in srgb, var(--accent) 25%, transparent);
  }

  /* ── Footer ── */
  footer {
    margin-top: 32px; padding-top: 14px;
    border-top: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    font-size: 11px; color: var(--muted);
  }
  footer a { color: var(--muted); }
  footer a:hover { color: var(--accent); text-decoration: none; }

  /* ── Minion watermark (always visible in Now Processing area) ── */
  #now-idle, #now-active { position: relative; overflow: hidden; }
  .minion-watermark {
    position: absolute; bottom: -8px; right: -4px;
    width: 90px; height: 90px; opacity: .07; pointer-events: none;
  }
  [data-theme="light"] .minion-watermark { opacity: .05; }

  /* ── Scrollbars ── */
  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--muted); }

  @media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }
</style>
</head>
<body>

<!-- ── Header ─────────────────────────────────────────────────────────────── -->
<header>
  <div style="display:flex;align-items:center;">
    <span class="logo">🧪</span>
    <div>
      <h1>Gru's Minion Lab</h1>
      <div class="sub"><span id="project-name">Loading…</span> · <code id="container-name">__CONTAINER__</code></div>
    </div>
  </div>
  <div class="right">
    <div style="display:flex;align-items:center;gap:8px;">
      <div id="status-dot" class="dot dot-muted"></div>
      <span id="status-text" class="text-sm col-muted">Checking…</span>
    </div>
    <span id="last-refresh" class="text-xs col-muted"></span>
    <button class="theme-btn" id="theme-btn" title="Toggle light/dark theme">☀</button>
  </div>
</header>

<!-- ── Now Processing + Queue ─────────────────────────────────────────────── -->
<div class="grid-2 mb-5">

  <!-- Now Processing (active) -->
  <div id="now-active" class="card card-active hidden">
    <!-- Minion watermark (Option 3) -->
    <svg class="minion-watermark" viewBox="0 0 100 130" xmlns="http://www.w3.org/2000/svg" fill="currentColor">
      <path d="M36 22 L30 4 L40 20 M50 20 L50 2 L57 19 M64 22 L68 4 L61 20" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
      <ellipse cx="50" cy="52" rx="32" ry="36"/>
      <rect x="14" y="40" width="72" height="16" rx="8"/>
      <circle cx="50" cy="48" r="16"/>
      <circle cx="50" cy="48" r="11" fill="var(--bg)"/>
      <circle cx="50" cy="48" r="7"/>
      <circle cx="53" cy="45" r="2.5" fill="var(--bg)"/>
      <path d="M38 72 Q50 82 62 72" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
      <rect x="20" y="84" width="60" height="44" rx="8"/>
      <rect x="38" y="86" width="24" height="16" rx="4" fill="var(--bg)" opacity=".3"/>
      <rect x="28" y="78" width="10" height="12" rx="3"/>
      <rect x="62" y="78" width="10" height="12" rx="3"/>
      <ellipse cx="36" cy="130" rx="14" ry="7"/>
      <ellipse cx="64" cy="130" rx="14" ry="7"/>
    </svg>
    <div class="flex items-center gap-2 mb-3">
      <div class="spinner"></div>
      <span class="section-label" style="margin:0;color:var(--accent)">Now Processing</span>
    </div>
    <div class="flex items-baseline gap-2 mb-1">
      <span id="now-num" class="now-num">#—</span>
      <span id="now-badge" class="badge badge-unknown"></span>
    </div>
    <div id="now-title" class="text-sm leading-snug mb-2 col-text"></div>
    <div id="now-elapsed" class="text-xs col-muted"></div>
  </div>

  <!-- Now Processing (idle) -->
  <div id="now-idle" class="card" style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;min-height:110px;">
    <!-- Minion watermark -->
    <svg class="minion-watermark" viewBox="0 0 100 130" xmlns="http://www.w3.org/2000/svg" fill="currentColor">
      <path d="M36 22 L30 4 L40 20 M50 20 L50 2 L57 19 M64 22 L68 4 L61 20" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
      <ellipse cx="50" cy="52" rx="32" ry="36"/>
      <rect x="14" y="40" width="72" height="16" rx="8"/>
      <circle cx="50" cy="48" r="16"/>
      <circle cx="50" cy="48" r="11" fill="var(--bg)"/>
      <circle cx="50" cy="48" r="7"/>
      <circle cx="53" cy="45" r="2.5" fill="var(--bg)"/>
      <path d="M38 72 Q50 82 62 72" stroke="currentColor" stroke-width="3" fill="none" stroke-linecap="round"/>
      <rect x="20" y="84" width="60" height="44" rx="8"/>
      <rect x="38" y="86" width="24" height="16" rx="4" fill="var(--bg)" opacity=".3"/>
      <rect x="28" y="78" width="10" height="12" rx="3"/>
      <rect x="62" y="78" width="10" height="12" rx="3"/>
      <ellipse cx="36" cy="130" rx="14" ry="7"/>
      <ellipse cx="64" cy="130" rx="14" ry="7"/>
    </svg>
    <span style="font-size:24px;">💤</span>
    <span class="text-sm col-muted">Awaiting orders…</span>
    <div id="sleep-countdown" class="hidden text-center mt-1">
      <div class="text-xs col-muted mb-1">Next scan in</div>
      <div id="countdown-value" class="text-2xl font-mono font-bold col-accent"></div>
    </div>
  </div>

  <!-- Queue -->
  <div class="card flex flex-col">
    <div class="flex items-center gap-2 mb-3">
      <span class="section-label" style="margin:0">Queue</span>
      <span id="queue-count" style="font-size:10px;padding:1px 7px;border-radius:20px;background:var(--surface2);border:1px solid var(--border2);color:var(--muted);">0</span>
      <span class="text-xs col-muted ml-auto">Todo — next to process</span>
    </div>
    <div id="queue-list" class="flex-1">
      <p class="text-xs col-muted italic">No issues in Todo</p>
    </div>
  </div>
</div>

<!-- ── In Progress ─────────────────────────────────────────────────────────── -->
<div id="pipeline-section" class="hidden mb-5">
  <div class="section-label">In Progress</div>
  <div id="pipeline" class="flex flex-wrap gap-2"></div>
</div>

<!-- ── Completed this run ──────────────────────────────────────────────────── -->
<div id="done-section" class="hidden mb-5">
  <div class="section-label">Completed This Run</div>
  <div id="done-list" class="flex flex-wrap gap-2"></div>
</div>

<div class="divider"></div>

<!-- ── Issue Details + Session Events ────────────────────────────────────── -->
<div class="grid-2 mb-5" style="align-items:stretch;">

  <div style="display:flex;flex-direction:column;">
    <div class="card" style="flex:1;display:flex;flex-direction:column;">
      <div class="section-label" style="margin-bottom:10px;">Issue Details</div>
      <div id="issue-details" style="flex:1;">
        <p class="text-xs col-muted italic">No active issue</p>
      </div>
    </div>
  </div>

  <div style="display:flex;flex-direction:column;">
    <div class="card" style="flex:1;display:flex;flex-direction:column;min-height:200px;max-height:320px;">
      <div class="section-label" style="margin-bottom:8px;">Session Events</div>
      <div id="events-panel" style="flex:1;overflow-y:auto;">
        <p class="text-xs col-muted italic">Waiting for events…</p>
      </div>
    </div>
  </div>

</div>

<!-- ── Full log ────────────────────────────────────────────────────────────── -->
<div>
  <button class="log-toggle" onclick="toggleLog()">
    <span id="log-arrow">▶</span>
    <span style="color:var(--muted)">Full log</span>
  </button>
  <div id="log-section" class="hidden">
    <div class="card" style="padding:10px;margin-top:6px;">
      <div id="log-panel"></div>
    </div>
  </div>
</div>

<footer>
  <span>🧪 Gru's Minion Lab · Powered by <a href="https://githubnext.com/projects/copilot-workspace" target="_blank">GitHub Copilot</a></span>
  <span>docker-gru-env · <span id="footer-container" style="font-family:ui-monospace,monospace;font-size:10px;"></span></span>
</footer>

<script>
document.addEventListener('DOMContentLoaded', () => {
  const fc = document.getElementById('footer-container');
  const cn = document.getElementById('container-name');
  if (fc && cn) fc.textContent = cn.textContent;
});
</script>

<script>
function toggleLog() {
  const s = document.getElementById('log-section');
  const a = document.getElementById('log-arrow');
  const hidden = s.classList.toggle('hidden');
  a.textContent = hidden ? '▶' : '▼';
}
</script>
<script>
// ── Theme switcher ────────────────────────────────────────────────────────────
(function() {
  const stored = localStorage.getItem('gru-dash-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = stored || (prefersDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);
  function applyBtn(t) {
    const btn = document.getElementById('theme-btn');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '🌙';
  }
  applyBtn(theme);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    if (!localStorage.getItem('gru-dash-theme')) {
      const t = e.matches ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', t);
      applyBtn(t);
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    applyBtn(document.documentElement.getAttribute('data-theme') || 'dark');
    const btn = document.getElementById('theme-btn');
    if (btn) btn.onclick = () => {
      const cur = document.documentElement.getAttribute('data-theme') || 'dark';
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('gru-dash-theme', next);
      applyBtn(next);
    };
  });
})();
</script>
<script>
// ── Shared state ─────────────────────────────────────────────────────────────
let boardItems  = [];
let boardLoaded = false;   // true after first successful /api/board response
let stageOrder  = [];
let projName    = '';
let activeIssue = null;   // {num, stage, started}
let doneIssues  = [];
let elapsedTimer = null;
let sleepUntilMs = null;  // ms timestamp of next scan, or null
let countdownTimer = null;

// ── Helpers ──────────────────────────────────────────────────────────────────
const STAGE_BADGES = {
  'Todo':'badge-todo','HW-Check':'badge-check','HW-Update':'badge-update',
  'HW-Stress':'badge-stress','HW-Log':'badge-log','Review':'badge-review','Done':'badge-done',
};
function stageBadge(s){ return STAGE_BADGES[s] || 'badge-unknown'; }
function stageIcon(s){
  return {'Todo':'📋','HW-Check':'🔍','HW-Update':'⬆️','HW-Stress':'💪','HW-Log':'📊','Review':'👀','Done':'✅'}[s] || '📌';
}
function escHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function truncate(s, n){ return s.length > n ? s.slice(0, n) + '…' : s; }

// ── Render: Now Processing ───────────────────────────────────────────────────
function tickCountdown() {
  const el = document.getElementById('countdown-value');
  const sec = document.getElementById('sleep-countdown');
  if (!el || sleepUntilMs === null) return;
  const rem = Math.max(0, Math.round((sleepUntilMs - Date.now()) / 1000));
  const m = Math.floor(rem / 60), s = rem % 60;
  if (rem === 0) {
    el.textContent = 'scanning…';
    el.className = 'text-2xl font-mono font-bold col-green';
    // Auto-hide after 30 s — if still 0 the server will have cleared sleep_until
    setTimeout(() => {
      if (sleepUntilMs !== null && sleepUntilMs <= Date.now()) {
        sleepUntilMs = null;
        if (sec) sec.classList.add('hidden');
        if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
      }
    }, 30_000);
  } else {
    el.textContent = `${m}:${String(s).padStart(2, '0')}`;
    el.className = 'text-2xl font-mono font-bold col-accent';
  }
  sec.classList.remove('hidden');
}

function renderNow() {
  const active  = document.getElementById('now-active');
  const idle    = document.getElementById('now-idle');
  if (!activeIssue) {
    active.classList.add('hidden');
    idle.classList.remove('hidden');
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
    // Countdown
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    if (sleepUntilMs !== null) {
      tickCountdown();
      countdownTimer = setInterval(tickCountdown, 1000);
    } else {
      const sec = document.getElementById('sleep-countdown');
      if (sec) sec.classList.add('hidden');
    }
    return;
  }
  // Active issue — hide countdown
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
  const sec = document.getElementById('sleep-countdown');
  if (sec) sec.classList.add('hidden');
  sleepUntilMs = null;

  const item  = boardItems.find(i => i.number === activeIssue.num);
  // Parent/human-only issues are skipped by the watcher after logging "Starting session".
  // Guard the UI too so they never flash as active.
  if (item && (item.is_parent || (item.labels || []).includes('human-only'))) {
    active.classList.add('hidden');
    idle.classList.remove('hidden');
    return;
  }
  const title = item ? item.title : '';
  // Prefer board status (always current) over log-parsed stage (snapshot from session start)
  const stage = (item ? item.status : null) || activeIssue.stage || '';
  document.getElementById('now-num').textContent   = '#' + activeIssue.num;
  document.getElementById('now-title').textContent = title;
  const badge = document.getElementById('now-badge');
  badge.textContent  = stage;
  badge.className    = 'badge ' + stageBadge(stage);
  active.classList.remove('hidden');
  idle.classList.add('hidden');

  // Elapsed timer
  if (elapsedTimer) clearInterval(elapsedTimer);
  const [hh, mm, ss] = (activeIssue.started || '00:00:00').split(':').map(Number);
  const today   = new Date();
  const started = new Date(today.getFullYear(), today.getMonth(), today.getDate(), hh, mm, ss);
  function tick() {
    const el = document.getElementById('now-elapsed');
    if (!el) return;
    const diff = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
    const h = Math.floor(diff / 3600), m = Math.floor((diff % 3600) / 60), s = diff % 60;
    el.textContent = `Running for ${h ? h + 'h ' : ''}${m}m ${String(s).padStart(2,'0')}s`;
  }
  tick();
  elapsedTimer = setInterval(tick, 1000);
}

// ── Render: Queue ─────────────────────────────────────────────────────────────
function renderQueue() {
  const activeNum = activeIssue ? activeIssue.num : null;
  const queue = boardItems
    .filter(i => i.status === 'Todo' && i.state === 'OPEN' && i.number !== activeNum
                 && !i.is_parent && !(i.labels || []).includes('human-only'))
    .sort((a, b) => a.number - b.number);

  document.getElementById('queue-count').textContent = queue.length;

  const list = document.getElementById('queue-list');
  if (queue.length === 0) {
    list.innerHTML = '<p class="text-xs col-muted italic">No issues in Todo</p>';
    return;
  }
  list.innerHTML = queue.map((it, idx) => `
    <div class="queue-item">
      <span class="queue-pos">${idx + 1}.</span>
      <span class="queue-title">${escHtml(truncate(it.title, 60))}</span>
      <a href="${escHtml(it.url)}" target="_blank" class="issue-num">#${it.number}</a>
    </div>`).join('');
}

// ── Render: Pipeline ─────────────────────────────────────────────────────────
function renderPipeline() {
  const section = document.getElementById('pipeline-section');
  const pipe    = document.getElementById('pipeline');

  // Only show when the watcher is actively running something
  if (!activeIssue) { section.classList.add('hidden'); return; }

  const activeNum = activeIssue.num;
  let item = boardItems.find(i => i.number === activeNum);

  // Board may lag — synthesise a minimal item from watcher state
  if (!item || item.status === 'Todo' || item.status === 'Done') {
    item = item
      ? { ...item, status: activeIssue.stage || 'HW-Check' }
      : { number: activeNum, title: `Issue #${activeNum}`, url: '', status: activeIssue.stage || 'HW-Check' };
  }

  section.classList.remove('hidden');
  const href = item.url || `https://github.com/custom-repo/custom-repo-sensei-o/issues/${activeNum}`;
  const spin = '<div class="spinner" style="width:9px;height:9px;border-width:1.5px;flex-shrink:0"></div>';
  pipe.innerHTML = `<a href="${escHtml(href)}" target="_blank" class="done-chip running">${spin}<strong>#${activeNum}</strong></a>`;
}

// ── Render: Completed ─────────────────────────────────────────────────────────
function renderCompleted() {
  // Only show issues that belong to the current project board.
  // If the board hasn't loaded yet, hide the section rather than showing
  // unfiltered history (avoids a flash of cross-board issues on startup).
  const boardNums = new Set(boardItems.map(i => i.number));
  const filtered  = boardLoaded
    ? doneIssues.filter(it => boardNums.has(it.num))
    : [];

  if (!filtered.length) {
    document.getElementById('done-section').classList.add('hidden');
    document.getElementById('stats-section').classList.add('hidden');
    return;
  }
  document.getElementById('done-section').classList.remove('hidden');
  // Deduplicate by issue number — keep the last (final) entry per issue
  const seen = new Map();
  filtered.forEach(it => seen.set(it.num, it));
  const unique = [...seen.values()];
  document.getElementById('done-list').innerHTML = unique.map(it => {
    const ok   = it.status === 'completed';
    const href = `https://github.com/custom-repo/custom-repo-sensei-o/issues/${it.num}`;
    return `<a href="${href}" target="_blank" class="done-chip ${ok ? 'ok' : 'err'}" style="text-decoration:none;">
      ${ok ? '✓' : '✗'} <strong>#${it.num}</strong>
    </a>`;
  }).join('');
  renderCharts();
}

function renderCharts() {}

// ── Data fetching ─────────────────────────────────────────────────────────────
async function refreshDevices() {}

async function refreshBoard() {
  try {
    const d = await fetch('/api/board').then(r => r.json());
    if (d.error) { console.warn('Board:', d.error); return; }
    boardItems  = d.items || [];
    boardLoaded = true;
    stageOrder = d.stage_order || [];
    projName   = d.project_name || 'Watcher Board';
    document.getElementById('project-name').textContent = projName;
    document.getElementById('last-refresh').textContent = 'Updated ' + new Date().toLocaleTimeString();
    renderQueue();
    renderPipeline();
    renderCompleted();
    renderIssueDetails();
  } catch(e) { console.error('Board fetch error', e); }
}

async function refreshStatus() {
  try {
    const d = await fetch('/api/status').then(r => r.json());
    const dot = document.getElementById('status-dot');
    const txt = document.getElementById('status-text');
    if (d.running) {
      dot.className = 'dot dot-green pulse';
      txt.textContent = 'Running'; txt.className = 'text-sm col-green';
    } else {
      dot.className = 'dot dot-muted';
      txt.textContent = 'Stopped'; txt.className = 'text-sm col-muted';
    }
    activeIssue = d.active || null;
    doneIssues  = d.done   || [];
    sleepUntilMs = (d.sleep_until != null && !activeIssue)
      ? d.sleep_until * 1000
      : null;
    renderNow();
    renderCompleted();
    renderPipeline();
    renderIssueDetails();
    renderEvents(d.events || []);
  } catch(e) { console.error('Status fetch error', e); }
}

// ── Render: Issue Details ─────────────────────────────────────────────────────
function renderIssueDetails() {
  const panel = document.getElementById('issue-details');
  if (!activeIssue) {
    panel.innerHTML = '<p class="text-xs col-muted italic">No active issue</p>';
    return;
  }
  const item = boardItems.find(i => i.number === activeIssue.num);
  if (!item) {
    panel.innerHTML = `<p class="text-sm col-muted">Issue #${activeIssue.num} — loading…</p>`;
    return;
  }
  const labelHtml = item.labels.map(l =>
    `<span class="badge badge-unknown" style="margin-right:4px;">${escHtml(l)}</span>`
  ).join('');
  const body = item.body || '';
  // Extract device table rows if present (lines starting with |)
  const tableLines = body.split('\n').filter(l => l.trim().startsWith('|') && !/^[\s|:-]+$/.test(l.trim()));
  const devSection = tableLines.length > 1
    ? `<div style="margin-top:10px;overflow-x:auto;"><table class="detail-table" style="border-collapse:collapse;">
        ${tableLines.slice(0,10).map((row, i) => {
          const cells = row.split('|').filter(c => c.trim());
          const tag = i === 0 ? 'th' : 'td';
          return `<tr>${cells.map(c => `<${tag} style="border:1px solid var(--border);padding:4px 8px;color:var(--text);font-size:11px;text-align:left;">${escHtml(c.trim())}</${tag}>`).join('')}</tr>`;
        }).join('')}
      </table></div>`
    : (body ? `<p class="text-xs col-muted" style="margin-top:8px;line-height:1.6;">${escHtml(truncate(body.replace(/#{1,6}\s/g,'').replace(/\n+/g,' '),300))}</p>` : '');

  panel.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px;">
      <a href="${escHtml(item.url)}" target="_blank"
         class="font-bold col-accent" style="font-size:15px;">#${item.number}</a>
      <span class="text-sm col-text leading-snug" style="flex:1;">${escHtml(item.title)}</span>
    </div>
    <div style="margin-bottom:8px;">${labelHtml}</div>
    ${devSection}`;
}

// ── Render: Session Events ────────────────────────────────────────────────────
function renderEvents(events) {
  const panel = document.getElementById('events-panel');
  if (!events.length) {
    panel.innerHTML = '<p class="text-xs col-muted italic">No events yet</p>';
    return;
  }
  panel.innerHTML = [...events].reverse().map(ev => {
    const cls = /✓/.test(ev.line)              ? 'ev-ok'
              : /✗|ERROR|failed/.test(ev.line) ? 'ev-fail'
              : /WARNING|WARN/.test(ev.line)   ? 'ev-warn'
              : /Starting session/.test(ev.line)? 'ev-start'
              : /⏸|re-poll/.test(ev.line)      ? 'ev-idle'
              : '';
    return `<div class="event-row">
      <span class="event-time">${escHtml(ev.t)}</span>
      <span class="event-line ${cls}">${escHtml(ev.line.trim())}</span>
    </div>`;
  }).join('');
}

// ── Live log SSE ──────────────────────────────────────────────────────────────
const logPanel  = document.getElementById('log-panel');
const autoScroll = document.getElementById('autoscroll') || { checked: true };

function lineClass(line) {
  if (/✓ Issue/.test(line))              return 'log-line log-success';
  if (/✗ Issue/.test(line))              return 'log-line log-fail';
  if (/Starting session/.test(line))     return 'log-line log-start';
  if (/⚠|WARNING|WARN/.test(line))       return 'log-line log-warn';
  if (/⊘|SKIP|skipped|⏸/.test(line))    return 'log-line log-skip';
  return 'log-line';
}
function appendLog(line) {
  const el = document.createElement('div');
  el.className = lineClass(line);
  el.textContent = line;
  logPanel.appendChild(el);
  if (logPanel.children.length > 1000) logPanel.removeChild(logPanel.firstChild);
  if (autoScroll.checked) logPanel.scrollTop = logPanel.scrollHeight;
}
const evtSrc = new EventSource('/api/logs');
evtSrc.onmessage = e => appendLog(JSON.parse(e.data));
evtSrc.onerror   = () => appendLog('── SSE connection lost, reconnecting… ──');

// ── Init ──────────────────────────────────────────────────────────────────────
refreshBoard();
refreshStatus();
refreshDevices();
setInterval(refreshBoard,   30_000);
setInterval(refreshStatus,   5_000);
setInterval(refreshDevices, 10_000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(f'Usage: {sys.argv[0]} <container> <port> [--config PATH]', file=sys.stderr)
        sys.exit(1)

    container = args[0]
    port      = int(args[1])
    config_path = None
    device_status_path = None
    i = 2
    while i < len(args):
        if args[i] == '--config' and i + 1 < len(args):
            config_path = args[i + 1]; i += 2
        elif args[i] == '--device-status' and i + 1 < len(args):
            device_status_path = args[i + 1]; i += 2
        else:
            i += 1

    cfg = load_config(config_path)
    if not device_status_path and cfg.get('device_status_file'):
        device_status_path = cfg['device_status_file']

    board = BoardCache(cfg)
    logs  = LogBuffer(container)

    class _Handler(Handler):
        pass
    _Handler.board  = board
    _Handler.logs   = logs
    _Handler.config = cfg
    _Handler.device_status_path = device_status_path

    server = ThreadingHTTPServer(('0.0.0.0', port), _Handler)
    print(f'Dashboard → http://localhost:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        board.stop()


if __name__ == '__main__':
    main()
