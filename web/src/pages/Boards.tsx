import { useEffect, useState, useCallback, useRef } from 'react'
import { RefreshCw, Play, Clock, CheckCircle2, Zap, Plus, Trash2, Edit2, X, Wand2, Send, User, Terminal } from 'lucide-react'

// ── Issue classifier ──────────────────────────────────────────────────────────
// The pipeline exposes classifier rules; the board uses them without knowing
// pipeline internals. This is the contract between Pipeline and Board.

interface ClassifierConfig {
  humanStages: string[]   // stage/column names configured as actor='human'
  humanLabels: string[]   // labels that signal human intervention needed
}

type IssueCategory = 'active' | 'waiting_human' | 'ai_queue'

/**
 * Pure classifier function — no side effects, no knowledge of pipeline internals.
 * Takes the pipeline's classifier config and classifies a single issue.
 */
function classifyIssue(
  issue: { stage?: string; labels?: string[] },
  config: ClassifierConfig,
): IssueCategory {
  if (config.humanStages.includes(issue.stage ?? '')) return 'waiting_human'
  if (issue.labels?.some(l => config.humanLabels.includes(l))) return 'waiting_human'
  return 'ai_queue'
}

/**
 * Partition the queued list from the status API into ai_queue and waiting_human.
 * active issues come directly from status.active (engine tracks them separately).
 */
function partitionQueue(
  queued: any[],
  config: ClassifierConfig,
): { aiQueue: any[]; waitingHuman: any[] } {
  const aiQueue: any[] = []
  const waitingHuman: any[] = []
  for (const item of queued) {
    if (classifyIssue(item, config) === 'waiting_human') waitingHuman.push(item)
    else aiQueue.push(item)
  }
  return { aiQueue, waitingHuman }
}

// ── Time formatter — compact relative + absolute fallback ─────────────────────

function fmtTime(iso: string | undefined | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const diffMs = Date.now() - d.getTime()
  const diffMin = Math.floor(diffMs / 60_000)
  if (diffMin < 1)   return 'just now'
  if (diffMin < 60)  return `${diffMin}m ago`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24)    return `${diffH}h ago`
  const diffD = Math.floor(diffH / 24)
  if (diffD < 7)     return `${diffD}d ago`
  // Older: show "Jul 2" or "Dec 31 '24"
  const now = new Date()
  const sameYear = d.getFullYear() === now.getFullYear()
  const month = d.toLocaleString('en', { month: 'short' })
  const day = d.getDate()
  return sameYear ? `${month} ${day}` : `${month} ${day} '${String(d.getFullYear()).slice(2)}`
}

// ── Stage tag ─────────────────────────────────────────────────────────────────

function StageTag({ stage }: { stage: string }) {
  const colors: Record<string, string> = {
    'Todo': 'var(--muted)', 'HW-Check': '#6366f1', 'HW-Update': '#f59e0b',
    'HW-Stress': '#ef4444', 'HW-Log': '#8b5cf6', 'Review': '#10b981', 'Done': 'var(--green)',
  }
  const c = colors[stage] || 'var(--muted)'
  return (
    <span style={{
      background: `color-mix(in srgb, ${c} 15%, transparent)`,
      color: c, border: `1px solid color-mix(in srgb, ${c} 30%, transparent)`,
      borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
    }}>{stage}</span>
  )
}

// ── Issue row ─────────────────────────────────────────────────────────────────

function IssueRow({ item, dim, showMeta }: { item: any; dim?: boolean; showMeta?: boolean }) {
  // Queue items carry updated_at (GitHub); recent items carry ended_at (our run)
  const ts = item.ended_at || item.updated_at || item.started_at || null
  const timeStr = fmtTime(ts)

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
      borderBottom: '1px solid var(--border)', opacity: dim ? 0.5 : 1,
    }}>
      <span style={{ color: 'var(--muted)', fontFamily: 'monospace', fontSize: 12, minWidth: 40 }}>#{item.number || item.issue_number || '—'}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.title || item.issue_title || item.name || item.stage || ''}
        </div>
        {showMeta && item.model && (
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
            <span>⚡ {item.model}</span>
          </div>
        )}
      </div>
      {timeStr && (
        <span style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap', flexShrink: 0 }} title={ts}>
          {timeStr}
        </span>
      )}
      <StageTag stage={item.stage || item.column || '?'} />
    </div>
  )
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionHeader({ icon, label, count, color }: {
  icon: React.ReactNode; label: string; count?: number; color: string
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700,
      color, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
    }}>
      {icon}
      {label}{count !== undefined ? ` (${count})` : ''}
    </div>
  )
}

// ── Live session log panel ────────────────────────────────────────────────────

interface LogEntry { level: string; message: string; timestamp: string; issue?: number; stage?: string }

const LEVEL_STYLE: Record<string, { color: string; prefix: string }> = {
  info:        { color: 'var(--muted)',  prefix: 'ℹ' },
  warn:        { color: 'var(--yellow)', prefix: '⚠' },
  error:       { color: 'var(--red)',    prefix: '✕' },
  success:     { color: 'var(--green)',  prefix: '✓' },
  session_log: { color: 'var(--fg)',     prefix: '›' },
}

function SessionLogPanel({ pipelineId, active }: { pipelineId: string; active: any | null }) {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    // Open SSE connection for this pipeline
    const es = new EventSource(`/api/pipelines/${pipelineId}/logs`)
    esRef.current = es

    const handle = (e: MessageEvent, level: string) => {
      try {
        const d = JSON.parse(e.data)
        setEntries(prev => {
          const next = [...prev, { level, message: d.message || '', timestamp: d.timestamp || '', issue: d.issue, stage: d.stage }]
          return next.slice(-600) // keep last 600 lines
        })
      } catch {}
    }

    const levels = ['info', 'warn', 'error', 'success', 'session_log', 'warning']
    levels.forEach(lv => es.addEventListener(lv, (e: Event) => handle(e as MessageEvent, lv === 'warning' ? 'warn' : lv)))

    return () => { es.close(); esRef.current = null }
  }, [pipelineId])

  // Auto-scroll to bottom when entries change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  // Clear log when a new active session starts
  const prevActiveRef = useRef<number | null>(null)
  useEffect(() => {
    const issueNum = active?.number ?? null
    if (issueNum !== prevActiveRef.current && issueNum !== null) {
      setEntries([])
    }
    prevActiveRef.current = issueNum
  }, [active?.number])

  if (!active && entries.length === 0) return null

  // Filter: when active, show session_log + structured events; when idle show only structured
  const visible = active
    ? entries.slice(-300)
    : entries.filter(e => e.level !== 'session_log').slice(-100)

  return (
    <div style={{
      marginTop: 16,
      border: '1px solid var(--border)',
      borderRadius: 8,
      overflow: 'hidden',
      background: 'color-mix(in srgb, var(--bg) 60%, transparent)',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '7px 12px', borderBottom: '1px solid var(--border)',
        background: 'var(--card)',
      }}>
        <Terminal size={13} color="var(--accent)" />
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em', flex: 1 }}>
          {active ? `Live Session — #${active.number || ''} · ${active.stage || ''}` : 'Session Log'}
        </span>
        {active && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--green)' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', animation: 'pulse 1.5s ease-in-out infinite' }} />
            Live
          </span>
        )}
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{visible.length} lines</span>
      </div>

      {/* Log scroll area */}
      <div style={{
        height: 300, overflowY: 'auto', padding: '8px 0',
        fontFamily: 'monospace', fontSize: 12,
      }}>
        {visible.length === 0 ? (
          <div style={{ color: 'var(--muted)', padding: '16px 14px', fontSize: 12 }}>
            Waiting for session output…
          </div>
        ) : visible.map((e, i) => {
          const style = LEVEL_STYLE[e.level] || LEVEL_STYLE.info
          const isSessionLog = e.level === 'session_log'
          return (
            <div key={i} style={{
              display: 'flex', gap: 8, padding: '1px 14px',
              alignItems: 'flex-start',
              background: i % 2 === 0 ? 'transparent' : 'color-mix(in srgb, var(--border) 20%, transparent)',
            }}>
              {!isSessionLog && (
                <span style={{ color: style.color, flexShrink: 0, fontSize: 11, marginTop: 1 }}>{style.prefix}</span>
              )}
              {isSessionLog && (
                <span style={{ color: 'var(--muted)', flexShrink: 0, fontSize: 10, marginTop: 2, minWidth: 10 }}>›</span>
              )}
              <span style={{
                color: isSessionLog ? 'var(--fg)' : style.color,
                opacity: isSessionLog ? 0.85 : 1,
                wordBreak: 'break-word', flex: 1, lineHeight: 1.5,
                fontWeight: isSessionLog ? 400 : 500,
              }}>{e.message}</span>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ── Pipeline activity card ────────────────────────────────────────────────────

function PipelineActivity({ pipeline }: { pipeline: any }) {
  const [status, setStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const r = await fetch(`/api/pipelines/${pipeline.id}/status`)
      setStatus(r.ok ? await r.json() : null)
    } catch { setStatus(null) }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [pipeline.id])

  // Build classifier config from what the pipeline tells us — board never hardcodes these
  const classifierConfig: ClassifierConfig = {
    humanStages: status?.classifier?.human_stages ?? [],
    humanLabels: status?.classifier?.human_labels ?? [],
  }

  const active: any[] = status?.active ? [status.active] : []
  const { aiQueue, waitingHuman } = partitionQueue(status?.queued || [], classifierConfig)
  const recent: any[] = (status?.recent || []).slice(0, 5)
  const isEmpty = active.length === 0 && aiQueue.length === 0 && waitingHuman.length === 0 && recent.length === 0

  const QUEUE_SHOW = 8

  return (
    <div className="card" style={{ padding: '16px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <span style={{ fontSize: 18 }}>🐙</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>{pipeline.name}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
            {pipeline.status === 'running' ? '● Running' : pipeline.status === 'paused' ? '⏸ Paused' : pipeline.status}
          </div>
        </div>
        <button className="btn btn-ghost" style={{ padding: '4px 8px' }} onClick={load}>
          <RefreshCw size={12} />
        </button>
      </div>

      {loading && <div style={{ display: 'flex', gap: 8, color: 'var(--muted)', fontSize: 13 }}><div className="spinner" style={{ width: 14, height: 14 }} /> Loading…</div>}
      {!loading && isEmpty && <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '16px 0' }}>No activity yet — pipeline is idle.</div>}

      {/* Active — currently being processed by AI agents */}
      {!loading && active.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <SectionHeader icon={<Play size={11} />} label="Active" color="var(--green)" />
          {active.map((item, i) => <IssueRow key={i} item={item} showMeta />)}
        </div>
      )}

      {/* Live session log — shown when active or when log has content */}
      <SessionLogPanel pipelineId={pipeline.id} active={active[0] ?? null} />

      {/* Waiting for Human — human stages or human-signal labels */}
      {!loading && waitingHuman.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <SectionHeader icon={<User size={11} />} label="Waiting for Human" count={waitingHuman.length} color="var(--yellow)" />
          {waitingHuman.slice(0, QUEUE_SHOW).map((item, i) => <IssueRow key={i} item={item} />)}
          {waitingHuman.length > QUEUE_SHOW && (
            <div style={{ fontSize: 12, color: 'var(--muted)', paddingTop: 6 }}>+{waitingHuman.length - QUEUE_SHOW} more</div>
          )}
        </div>
      )}

      {/* AI Queue — waiting for pipeline agents to pick up */}
      {!loading && aiQueue.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <SectionHeader icon={<Clock size={11} />} label="AI Queue" count={aiQueue.length} color="var(--accent)" />
          {aiQueue.slice(0, QUEUE_SHOW).map((item, i) => <IssueRow key={i} item={item} />)}
          {aiQueue.length > QUEUE_SHOW && (
            <div style={{ fontSize: 12, color: 'var(--muted)', paddingTop: 6 }}>+{aiQueue.length - QUEUE_SHOW} more</div>
          )}
        </div>
      )}

      {/* Recently processed */}
      {!loading && recent.length > 0 && (
        <div>
          <SectionHeader icon={<CheckCircle2 size={11} />} label="Recently Processed" color="var(--muted)" />
          {recent.map((item, i) => <IssueRow key={i} item={item} dim />)}
        </div>
      )}
    </div>
  )
}

// ── Quick Actions panel ───────────────────────────────────────────────────────

interface QuickAction {
  id: string
  name: string
  action_type: string
  pipeline_id: string
  config: { stage?: string; repo?: string; labels?: string[]; skill?: string }
}

function QuickActionsPanel({ pipelines }: { pipelines: any[] }) {
  const [actions, setActions] = useState<QuickAction[]>([])
  const [editing, setEditing] = useState<Partial<QuickAction> | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [skills, setSkills] = useState<{ id: string; files: string[] }[]>([])

  // Run state for expanded action
  const [title, setTitle] = useState('')
  const [extraCtx, setExtraCtx] = useState('')
  const [generatedBody, setGeneratedBody] = useState('')
  const [generating, setGenerating] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [publishResult, setPublishResult] = useState<{ url?: string; number?: number; message?: string; error?: string } | null>(null)

  const load = useCallback(() => {
    fetch('/api/quick-actions').then(r => r.json()).then(d => setActions(Array.isArray(d) ? d : [])).catch(() => {})
    fetch('/api/skills').then(r => r.json()).then(d => setSkills(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  // Stages for selected pipeline
  const pipelineStages = (pid: string) => {
    const p = pipelines.find(p => p.id === pid)
    return (p?.stages || []).map((s: any) => s.column_name || s.column).filter(Boolean)
  }

  async function saveAction() {
    if (!editing) return
    const method = isNew ? 'POST' : 'PUT'
    const url = isNew ? '/api/quick-actions' : `/api/quick-actions/${editing.id}`
    const r = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editing),
    })
    if (r.ok) { setEditing(null); setIsNew(false); load() }
  }

  async function deleteAction(id: string) {
    if (!confirm('Delete this quick action?')) return
    await fetch(`/api/quick-actions/${id}`, { method: 'DELETE' })
    load()
  }

  function startNew() {
    setEditing({ name: 'New action', action_type: 'create_issue', pipeline_id: pipelines[0]?.id || '', config: {} })
    setIsNew(true)
  }


  async function generate(a: QuickAction) {
    setGenerating(true); setGeneratedBody(''); setPublishResult(null)
    try {
      const r = await fetch('/api/quick-actions/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pipeline_id: a.pipeline_id, stage: a.config.stage || '', title, extra_context: extraCtx, skill: a.config.skill || '' }),
      })
      const d = await r.json()
      if (!r.ok) setGeneratedBody(`❌ ${d.detail || 'Generation failed'}`)
      else setGeneratedBody(d.body || '')
    } catch { setGeneratedBody('Error calling generate endpoint') }
    finally { setGenerating(false) }
  }

  async function publish(a: QuickAction) {
    setPublishing(true); setPublishResult(null)
    try {
      const r = await fetch('/api/quick-actions/publish', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pipeline_id: a.pipeline_id, stage: a.config.stage || '',
          repo: a.config.repo || '', title, body: generatedBody,
          labels: a.config.labels || [],
          skill: a.config.skill || '',
        }),
      })
      const d = await r.json()
      if (r.ok) {
        if (d.source === 'skill') setPublishResult({ message: d.message })
        else setPublishResult({ url: d.issue_url, number: d.issue_number })
        // Collapse the card after a short success flash
        setTimeout(() => {
          setExpanded(null)
          setPublishResult(null)
          setTitle('')
          setExtraCtx('')
          setGeneratedBody('')
        }, 2500)
      } else setPublishResult({ error: d.detail || 'Publish failed' })
    } catch (e: any) { setPublishResult({ error: e.message }) }
    finally { setPublishing(false) }
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Zap size={14} style={{ color: 'var(--yellow)' }} />
        <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>Quick Actions</span>
        <button className="btn btn-ghost" style={{ padding: '3px 7px', fontSize: 12 }} onClick={startNew}>
          <Plus size={12} /> New
        </button>
      </div>

      {/* Edit form */}
      {editing && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', marginBottom: 2 }}>{isNew ? 'New action' : 'Edit action'}</div>
          <input className="input" style={{ fontSize: 12 }} placeholder="Action name" value={editing.name || ''} onChange={e => setEditing(v => ({ ...v!, name: e.target.value }))} />
          <select className="input" style={{ fontSize: 12 }} value={editing.pipeline_id || ''} onChange={e => setEditing(v => ({ ...v!, pipeline_id: e.target.value }))}>
            <option value="">— Select pipeline —</option>
            {pipelines.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select className="input" style={{ fontSize: 12 }} value={editing.config?.stage || ''} onChange={e => setEditing(v => ({ ...v!, config: { ...v!.config, stage: e.target.value } }))}>
            <option value="">— Select stage —</option>
            {pipelineStages(editing.pipeline_id || '').map((s: string) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input className="input" style={{ fontSize: 12 }} placeholder="Repo (owner/repo)" value={editing.config?.repo || ''} onChange={e => setEditing(v => ({ ...v!, config: { ...v!.config, repo: e.target.value } }))} />
          <input className="input" style={{ fontSize: 12 }} placeholder="Labels (comma-separated)" value={(editing.config?.labels || []).join(', ')} onChange={e => setEditing(v => ({ ...v!, config: { ...v!.config, labels: e.target.value.split(',').map(s => s.trim()).filter(Boolean) } }))} />
          <div>
            <select className="input" style={{ fontSize: 12, width: '100%' }}
              value={editing.config?.skill || ''}
              onChange={e => setEditing(v => ({ ...v!, config: { ...v!.config, skill: e.target.value } }))}>
              <option value="">— No skill (use LLM) —</option>
              {skills.map(sk => (
                <option key={sk.id} value={sk.id}>{sk.id}</option>
              ))}
            </select>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3 }}>
              {editing.config?.skill ? `▶ skill: ${editing.config.skill}` : 'Generate via LLM if no skill selected'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn btn-primary" style={{ fontSize: 12, flex: 1 }} onClick={saveAction}>Save</button>
            <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => { setEditing(null); setIsNew(false) }}><X size={12} /></button>
          </div>
        </div>
      )}

      {/* Action list */}
      {actions.length === 0 && !editing && (
        <div style={{ padding: '24px 16px', color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>
          No quick actions yet — create one to inject issues into a pipeline stage
        </div>
      )}

      {actions.map(a => (
        <div key={a.id} style={{ borderBottom: '1px solid var(--border)', padding: '10px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Action header */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={12} style={{ color: 'var(--yellow)', flexShrink: 0 }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', flex: 1 }}>{a.name}</span>
            {a.config.stage && <StageTag stage={a.config.stage} />}
            <button className="btn btn-ghost" style={{ padding: '3px 6px' }} onClick={() => { setEditing({ ...a }); setIsNew(false) }}><Edit2 size={11} /></button>
            <button className="btn btn-ghost" style={{ padding: '3px 6px', color: 'var(--red)' }} onClick={() => deleteAction(a.id)}><Trash2 size={11} /></button>
          </div>

          {/* Always-visible run inputs */}
          {a.config.skill && (
            <div style={{ fontSize: 10, color: 'var(--green)', padding: '3px 7px', borderRadius: 4, background: 'color-mix(in srgb, var(--green) 10%, transparent)' }}>
              🔧 {a.config.skill}
            </div>
          )}
          <textarea className="input" rows={3} style={{ fontSize: 12, resize: 'vertical' }}
            placeholder="Your thoughts — describe what you want this issue to be about…"
            value={expanded === a.id ? extraCtx : ''}
            onChange={e => { setExpanded(a.id); setExtraCtx(e.target.value); setGeneratedBody(''); setPublishResult(null) }} />
          <input className="input" style={{ fontSize: 12 }} placeholder="Issue title (one line)"
            value={expanded === a.id ? title : ''}
            onChange={e => { setExpanded(a.id); setTitle(e.target.value); setGeneratedBody(''); setPublishResult(null) }} />

          <button className="btn btn-ghost" style={{ fontSize: 12, gap: 6, justifyContent: 'center' }}
            onClick={() => { setExpanded(a.id); generate(a) }} disabled={!title || generating || expanded !== a.id}>
            {generating && expanded === a.id ? <><div className="spinner" style={{ width: 12, height: 12 }} /> Generating…</> : <><Wand2 size={12} /> Generate issue body</>}
          </button>

          {expanded === a.id && generatedBody && (
            <>
              <textarea className="input" rows={8} style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace', resize: 'vertical' }}
                value={generatedBody} onChange={e => setGeneratedBody(e.target.value)} />
              <button className="btn btn-primary" style={{ fontSize: 12, gap: 6, justifyContent: 'center' }}
                onClick={() => publish(a)} disabled={publishing || !a.config.repo}>
                {publishing ? <><div className="spinner" style={{ width: 12, height: 12 }} /> Publishing…</> : <><Send size={12} /> Publish to board</>}
              </button>
              {!a.config.repo && <div style={{ fontSize: 11, color: 'var(--yellow)' }}>⚠ No repo configured — edit this action to add one</div>}
            </>
          )}

          {expanded === a.id && publishResult && (
            <div style={{ padding: '8px 10px', borderRadius: 6, fontSize: 12, background: publishResult.error ? 'color-mix(in srgb, var(--red) 10%, transparent)' : 'color-mix(in srgb, var(--green) 10%, transparent)', color: publishResult.error ? 'var(--red)' : 'var(--green)' }}>
              {publishResult.error ? `❌ ${publishResult.error}`
                : publishResult.message ? <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 11 }}>{publishResult.message}</pre>
                : <a href={publishResult.url} target="_blank" rel="noreferrer" style={{ color: 'var(--green)' }}>✅ Issue #{publishResult.number} created →</a>}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Main Boards page ──────────────────────────────────────────────────────────

export default function Boards() {
  const [pipelines, setPipelines] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    fetch('/api/pipelines').then(r => r.json()).then(d => { setPipelines(Array.isArray(d) ? d : []); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Boards</h1>
        <button className="btn btn-ghost" onClick={load}><RefreshCw size={14} /> Refresh</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 16, alignItems: 'start' }}>
        {/* Left: pipeline activity */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {loading && pipelines.length === 0 && (
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', color: 'var(--muted)' }}><div className="spinner" /> Loading…</div>
          )}
          {!loading && pipelines.length === 0 && (
            <div className="card" style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📋</div>
              No pipelines configured yet.
              <br /><a href="/#/pipelines" style={{ fontSize: 13, color: 'var(--accent)', marginTop: 12, display: 'inline-block' }}>Go to Pipelines →</a>
            </div>
          )}
          {pipelines.map((p: any) => <PipelineActivity key={p.id} pipeline={p} />)}
        </div>

        {/* Right: Quick Actions */}
        <div style={{ position: 'sticky', top: 16 }}>
          <QuickActionsPanel pipelines={pipelines} />
        </div>
      </div>
    </div>
  )
}
