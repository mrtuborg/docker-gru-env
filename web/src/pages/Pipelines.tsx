import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Play, Pause, RefreshCw, ChevronRight, ChevronDown,
         ExternalLink, Clock, CheckCircle2, XCircle,
         AlertTriangle, Info, Loader2 } from 'lucide-react'

interface Stage { column_name: string; actor: string; stage_index: number }
interface Pipeline {
  id: string; name: string; enabled: number; plugin_id: string
  project_owner: string | null; project_number: number | null
  poll_interval: number; stages: Stage[]
  models: { model: string; priority: number }[]
  findings: { project_owner: string; project_number: number; initial_status: string } | null
}
interface IssueItem { number: number; repo: string; stage: string; title?: string; started_at?: string; model?: string }
interface RunItem {
  issue_number: number; issue_repo: string; stage: string; status: string
  started_at: string; ended_at?: string; duration_s?: number; model?: string; run_id: string
}
interface PipelineStatus {
  status: string
  active: IssueItem | null
  queued: IssueItem[]
  recent: RunItem[]
}

// ── Log line level icons ──────────────────────────────────────────────────────
const LevelIcon = ({ level }: { level: string }) => {
  switch (level) {
    case 'success': return <CheckCircle2 size={12} color="var(--green)"/>
    case 'error':   return <XCircle size={12} color="var(--red)"/>
    case 'warn':    return <AlertTriangle size={12} color="var(--yellow)"/>
    default:        return <Info size={12} color="var(--accent)"/>
  }
}

// ── Inline SSE log panel ──────────────────────────────────────────────────────
function InlineLog({ pipelineId, onOpenFull }: { pipelineId: string; onOpenFull: () => void }) {
  const [lines, setLines] = useState<{ level: string; message: string; ts: string }[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const es = new EventSource(`/api/pipelines/${pipelineId}/logs`)
    es.addEventListener('info',    (e: Event) => addLine('info',    e as MessageEvent))
    es.addEventListener('success', (e: Event) => addLine('success', e as MessageEvent))
    es.addEventListener('warn',    (e: Event) => addLine('warn',    e as MessageEvent))
    es.addEventListener('error',   (e: Event) => addLine('error',   e as MessageEvent))
    return () => es.close()

    function addLine(level: string, e: MessageEvent) {
      try {
        const d = JSON.parse(e.data)
        setLines(prev => [...prev.slice(-199), { level, message: d.message, ts: d.timestamp?.slice(11, 19) || '' }])
      } catch { /* ignore */ }
    }
  }, [pipelineId])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [lines])

  return (
    <div style={{
      margin: '8px 0 4px', borderRadius: 6,
      background: 'var(--surface2)', border: '1px solid var(--border)',
      overflow: 'hidden',
    }}>
      <div style={{
        maxHeight: 220, overflowY: 'auto', padding: '8px 12px',
        fontFamily: 'monospace', fontSize: 11, lineHeight: 1.6,
      }}>
        {lines.length === 0
          ? <span style={{ color: 'var(--muted)' }}>Waiting for log events…</span>
          : lines.map((l, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--muted)', flexShrink: 0 }}>{l.ts}</span>
                <LevelIcon level={l.level}/>
                <span>{l.message}</span>
              </div>
            ))
        }
        <div ref={bottomRef}/>
      </div>
      <div style={{
        padding: '4px 12px', borderTop: '1px solid var(--border)',
        display: 'flex', justifyContent: 'flex-end',
      }}>
        <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }} onClick={onOpenFull}>
          <ExternalLink size={11}/> Open full log
        </button>
      </div>
    </div>
  )
}

// ── Duration helper ───────────────────────────────────────────────────────────
function elapsed(iso?: string) {
  if (!iso) return ''
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}
function dur(s?: number) {
  if (!s) return ''
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

// ── Status dot ────────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: string }) {
  const color = status === 'success' ? 'var(--green)' : status === 'failure' || status === 'timeout' ? 'var(--red)' : 'var(--yellow)'
  const label = status === 'success' ? '✓' : status === 'failure' ? '✕' : status === 'timeout' ? '⏱' : '?'
  return <span style={{ color, fontWeight: 700, fontSize: 13 }}>{label}</span>
}

// ── Main pipeline card ────────────────────────────────────────────────────────
function PipelineCard({ p, onToggle }: { p: Pipeline; onToggle: () => void }) {
  const navigate = useNavigate()
  const [ps, setPs] = useState<PipelineStatus | null>(null)
  const [logOpen, setLogOpen] = useState(false)
  const [expandedRecent, setExpandedRecent] = useState<string | null>(null)

  const loadStatus = () => {
    fetch(`/api/pipelines/${p.id}/status`).then(r => r.json()).then(setPs).catch(() => {})
  }

  useEffect(() => {
    loadStatus()
    const t = setInterval(loadStatus, 8000)
    return () => clearInterval(t)
  }, [p.id])

  const ghLink = p.project_owner && p.project_number
    ? `https://github.com/orgs/${p.project_owner}/projects/${p.project_number}`
    : null

  const aiStages = p.stages.filter(s => s.actor === 'ai').map(s => s.column_name)
  const running = ps?.status === 'running'

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* ── Header ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        padding: '14px 20px', borderBottom: '1px solid var(--border)',
      }}>
        <span style={{ fontSize: 18 }}>🔬</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{p.name}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>
            {p.plugin_id}
            {p.project_owner && p.project_number && ` · ${p.project_owner}/#${p.project_number}`}
            {p.findings && ` → findings → #${p.findings.project_number}`}
            {ghLink && (
              <a href={ghLink} target="_blank" rel="noreferrer"
                 style={{ marginLeft: 6, color: 'var(--accent)' }}>
                <ExternalLink size={10}/>
              </a>
            )}
          </div>
        </div>
        <span className={`badge ${running ? 'badge-healthy' : 'badge-unknown'}`} style={{ fontSize: 10 }}>
          {running ? '● Running' : '⏸ Paused'}
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-ghost" style={{ fontSize: 11, padding: '4px 10px' }}
            onClick={() => navigate(`/pipelines/${p.id}`)}>Edit</button>
          <button className={`btn ${running ? 'btn-secondary' : 'btn-primary'}`}
            style={{ fontSize: 11, padding: '4px 10px' }} onClick={onToggle}>
            {running ? <><Pause size={11}/> Pause</> : <><Play size={11}/> Start</>}
          </button>
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ padding: '14px 20px' }}>

        {/* Meta line */}
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 12, display: 'flex', gap: 16 }}>
          <span>Stages: {aiStages.join(' → ')} → 🧑</span>
          {p.models?.[0] && <span>Model: {p.models[0].model}</span>}
        </div>

        {/* ── Active ── */}
        {ps?.active ? (
          <section style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>
              Active
            </div>
            <div
              onClick={() => setLogOpen(o => !o)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 12px', borderRadius: 8, cursor: 'pointer',
                background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
                border: '1px solid color-mix(in srgb, var(--accent) 25%, transparent)',
              }}
            >
              {logOpen ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}
              <Loader2 size={13} style={{ animation: 'spin 1.2s linear infinite', color: 'var(--accent)' }}/>
              <span style={{ fontWeight: 600, fontSize: 13 }}>#{ps.active.number}</span>
              {ps.active.title && <span style={{ color: 'var(--muted)', fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ps.active.title}</span>}
              <span style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 500 }}>{ps.active.stage}</span>
              {ps.active.started_at && <span style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 4 }}><Clock size={10}/>{elapsed(ps.active.started_at)}</span>}
              {ps.active.model && <span style={{ fontSize: 10, color: 'var(--muted)' }}>{ps.active.model}</span>}
            </div>
            {logOpen && <InlineLog pipelineId={p.id} onOpenFull={() => navigate(`/pipelines/${p.id}/logs`)}/>}
          </section>
        ) : running ? (
          <section style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>Active</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', padding: '6px 0' }}>Polling for issues…</div>
          </section>
        ) : null}

        {/* ── Queued ── */}
        {(ps?.queued?.length ?? 0) > 0 && (
          <section style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>
              Queued ({ps!.queued.length})
            </div>
            {ps!.queued.map(item => (
              <div key={`${item.repo}:${item.number}`} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '6px 12px', borderRadius: 6, marginBottom: 4,
                background: 'var(--surface2)', fontSize: 12,
              }}>
                <span style={{ color: 'var(--muted)' }}>📋</span>
                <span style={{ fontWeight: 500 }}>#{item.number}</span>
                {item.title && <span style={{ color: 'var(--muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.title}</span>}
                <span style={{ color: 'var(--muted)', fontSize: 11 }}>{item.stage}</span>
              </div>
            ))}
          </section>
        )}

        {/* ── Recent ── */}
        {(ps?.recent?.length ?? 0) > 0 && (
          <section>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>
              Recent
            </div>
            {ps!.recent.slice(0, 8).map((item) => {
              const key = `${item.run_id}:${item.issue_number}:${item.stage}`
              const open = expandedRecent === key
              return (
                <div key={key} style={{ marginBottom: 3 }}>
                  <div
                    onClick={() => setExpandedRecent(open ? null : key)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '5px 12px', borderRadius: 6, cursor: 'pointer',
                      background: 'var(--surface2)', fontSize: 12,
                    }}
                  >
                    {open ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}
                    <StatusDot status={item.status}/>
                    <span style={{ fontWeight: 500 }}>#{item.issue_number}</span>
                    <span style={{ color: 'var(--muted)', flex: 1 }}>{item.stage}</span>
                    {item.duration_s != null && <span style={{ color: 'var(--muted)', fontSize: 11 }}>{dur(item.duration_s)}</span>}
                    {item.model && <span style={{ color: 'var(--muted)', fontSize: 10 }}>{item.model}</span>}
                  </div>
                  {open && (
                    <div style={{
                      margin: '4px 0 4px', padding: '8px 12px',
                      background: 'var(--surface2)', borderRadius: 6,
                      fontSize: 11, color: 'var(--muted)', display: 'flex', gap: 16,
                    }}>
                      <span>Status: <strong style={{ color: item.status === 'success' ? 'var(--green)' : 'var(--red)' }}>{item.status}</strong></span>
                      {item.started_at && <span>Started: {new Date(item.started_at).toLocaleTimeString()}</span>}
                      {item.ended_at && <span>Ended: {new Date(item.ended_at).toLocaleTimeString()}</span>}
                    </div>
                  )}
                </div>
              )
            })}
            <button className="btn btn-ghost" style={{ fontSize: 11, padding: '4px 8px', marginTop: 4 }}
              onClick={() => navigate(`/pipelines/${p.id}/runs`)}>
              View all runs →
            </button>
          </section>
        )}

        {!ps && (
          <div style={{ color: 'var(--muted)', fontSize: 12 }}>Loading status…</div>
        )}
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────
export default function Pipelines() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    fetch('/api/pipelines').then(r => r.json()).then(setPipelines).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const togglePipeline = async (p: Pipeline) => {
    const endpoint = p.enabled ? 'stop' : 'start'
    await fetch(`/api/pipelines/${p.id}/${endpoint}`, { method: 'POST' })
    await fetch(`/api/pipelines/${p.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !p.enabled }),
    })
    load()
  }

  if (loading && pipelines.length === 0) return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center', color: 'var(--muted)', padding: 48 }}>
      <div className="spinner"/> Loading pipelines…
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Pipelines</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }}/> Refresh
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/pipelines/new')}>
            <Plus size={14}/> New Pipeline
          </button>
        </div>
      </div>

      {pipelines.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔬</div>
          <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8 }}>No pipelines configured</div>
          <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 20 }}>
            Pipelines connect a project board to AI agents. Each board column becomes a stage.
          </div>
          <button className="btn btn-primary" onClick={() => navigate('/pipelines/new')}>
            <Plus size={14}/> Create your first pipeline
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {pipelines.map(p => (
            <PipelineCard key={p.id} p={p} onToggle={() => togglePipeline(p)}/>
          ))}
        </div>
      )}
    </div>
  )
}
