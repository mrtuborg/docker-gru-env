import { useEffect, useState } from 'react'
import { RefreshCw, Play, Clock, CheckCircle2 } from 'lucide-react'

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

function IssueRow({ item, dim }: { item: any; dim?: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0',
      borderBottom: '1px solid var(--border)', opacity: dim ? 0.5 : 1,
    }}>
      <span style={{ color: 'var(--muted)', fontFamily: 'monospace', fontSize: 12, minWidth: 40 }}>#{item.number || item.issue_number || '—'}</span>
      <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {item.title || item.issue_title || item.name || '(untitled)'}
      </span>
      <StageTag stage={item.stage || item.column || '?'} />
    </div>
  )
}

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

  const active: any[] = status?.active ? [status.active] : []
  const queued: any[] = status?.queued || []
  const recent: any[] = (status?.recent || []).slice(0, 5)
  const isEmpty = active.length === 0 && queued.length === 0 && recent.length === 0

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

      {!loading && isEmpty && (
        <div style={{ color: 'var(--muted)', fontSize: 13, textAlign: 'center', padding: '16px 0' }}>
          No activity yet — pipeline is idle.
        </div>
      )}

      {!loading && active.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, color: 'var(--green)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
            <Play size={11} /> Active
          </div>
          {active.map((item, i) => <IssueRow key={i} item={item} />)}
        </div>
      )}

      {!loading && queued.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
            <Clock size={11} /> Queued ({queued.length})
          </div>
          {queued.slice(0, 8).map((item, i) => <IssueRow key={i} item={item} />)}
          {queued.length > 8 && <div style={{ fontSize: 12, color: 'var(--muted)', paddingTop: 6 }}>+{queued.length - 8} more</div>}
        </div>
      )}

      {!loading && recent.length > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
            <CheckCircle2 size={11} /> Recently processed
          </div>
          {recent.map((item, i) => <IssueRow key={i} item={item} dim />)}
        </div>
      )}
    </div>
  )
}

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

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {pipelines.map((p: any) => <PipelineActivity key={p.id} pipeline={p} />)}
      </div>
    </div>
  )
}
