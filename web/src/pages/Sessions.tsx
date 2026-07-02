import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, DollarSign, Clock, Activity, CheckCircle2, XCircle, ChevronDown } from 'lucide-react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDur(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

function fmtCost(usd: number | null | undefined): string {
  if (usd == null) return '—'
  if (usd === 0) return '$0'
  return `$${usd.toFixed(4)}`
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const diff = Math.floor((Date.now() - d.getTime()) / 60_000)
  if (diff < 1) return 'just now'
  if (diff < 60) return `${diff}m ago`
  const h = Math.floor(diff / 60)
  if (h < 24) return `${h}h ago`
  const dy = Math.floor(h / 24)
  if (dy < 7) return `${dy}d ago`
  const now = new Date()
  const mo = d.toLocaleString('en', { month: 'short' })
  return d.getFullYear() === now.getFullYear()
    ? `${mo} ${d.getDate()}`
    : `${mo} ${d.getDate()} '${String(d.getFullYear()).slice(2)}`
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, sub, color = 'var(--accent)' }: {
  icon: any; label: string; value: string | number; sub?: string; color?: string
}) {
  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 16px' }}>
      <div style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        background: `color-mix(in srgb, ${color} 15%, transparent)`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Icon size={17} color={color} />
      </div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1 }}>{value}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  )
}

// ── Mini bar list (stage / model breakdown) ───────────────────────────────────

function BreakdownList({ title, items, maxCost }: {
  title: string
  items: { label: string; count: number; cost: number; succeeded: number }[]
  maxCost: number
}) {
  return (
    <div className="card" style={{ padding: '14px 16px', flex: 1, minWidth: 220 }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)', marginBottom: 12 }}>{title}</div>
      {items.length === 0
        ? <div style={{ color: 'var(--muted)', fontSize: 12 }}>No data</div>
        : items.map(item => {
          const pct = maxCost > 0 ? (item.cost / maxCost) * 100 : 0
          const failCount = item.count - item.succeeded
          return (
            <div key={item.label} style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
                <span style={{ fontSize: 12, fontWeight: 500, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.label}>{item.label}</span>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11 }}>
                  {item.succeeded > 0 && <span style={{ color: 'var(--green)' }}>✓{item.succeeded}</span>}
                  {failCount > 0 && <span style={{ color: 'var(--red)' }}>✕{failCount}</span>}
                  <span style={{ color: 'var(--muted)', fontFamily: 'monospace' }}>{fmtCost(item.cost)}</span>
                </div>
              </div>
              <div style={{ height: 4, borderRadius: 2, background: 'var(--border)' }}>
                <div style={{ height: '100%', width: `${pct}%`, borderRadius: 2, background: 'var(--accent)', transition: 'width .3s' }} />
              </div>
            </div>
          )
        })}
    </div>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const ok = ['success', 'completed', 'done'].includes(status)
  const fail = ['failure', 'failed', 'error'].includes(status)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600 }}>
      {ok ? <CheckCircle2 size={12} color="var(--green)" />
        : fail ? <XCircle size={12} color="var(--red)" />
        : <Clock size={12} color="var(--muted)" />}
      <span style={{ color: ok ? 'var(--green)' : fail ? 'var(--red)' : 'var(--muted)' }}>{status}</span>
    </span>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

const DAYS_OPTIONS = [
  { label: 'Last 7 days', value: 7 },
  { label: 'Last 30 days', value: 30 },
  { label: 'All time', value: 0 },
]

export default function SessionsPage() {
  const [pipelines, setPipelines] = useState<any[]>([])
  const [pipelineId, setPipelineId] = useState<string>('')
  const [days, setDays] = useState(7)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  // Load pipeline list once
  useEffect(() => {
    fetch('/api/pipelines').then(r => r.json()).then((list: any[]) => {
      setPipelines(Array.isArray(list) ? list : [])
      if (list.length > 0) setPipelineId(list[0].id)
    }).catch(() => {})
  }, [])

  const load = useCallback(() => {
    if (!pipelineId) return
    setLoading(true)
    fetch(`/api/pipelines/${pipelineId}/sessions?days=${days}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [pipelineId, days])

  useEffect(() => { load() }, [load])

  const summary = data?.summary
  const sessions: any[] = data?.sessions ?? []

  // Breakdown lists
  const stageItems = Object.entries(summary?.by_stage ?? {}).map(([label, v]: [string, any]) => ({
    label, count: v.count, cost: v.cost_usd ?? 0, succeeded: v.succeeded ?? 0,
  }))
  const modelItems = Object.entries(summary?.by_model ?? {}).map(([label, v]: [string, any]) => ({
    label, count: v.count, cost: v.cost_usd ?? 0, succeeded: 0,
  }))
  const maxStageCost = Math.max(...stageItems.map(i => i.cost), 0.0001)
  const maxModelCost = Math.max(...modelItems.map(i => i.cost), 0.0001)

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, flex: 1, minWidth: 120 }}>Sessions & Cost</h1>

        {/* Pipeline picker */}
        {pipelines.length > 0 && (
          <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
            <select
              value={pipelineId}
              onChange={e => setPipelineId(e.target.value)}
              style={{
                appearance: 'none', background: 'var(--card)', border: '1px solid var(--border)',
                borderRadius: 7, padding: '6px 30px 6px 10px', fontSize: 13, fontWeight: 500,
                color: 'var(--fg)', cursor: 'pointer', minWidth: 150,
              }}
            >
              {pipelines.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
            </select>
            <ChevronDown size={14} style={{ position: 'absolute', right: 8, pointerEvents: 'none', color: 'var(--muted)' }} />
          </div>
        )}

        {/* Time range */}
        <div style={{ display: 'flex', background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 7, overflow: 'hidden' }}>
          {DAYS_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => setDays(opt.value)}
              style={{
                padding: '6px 12px', fontSize: 12, fontWeight: 500, border: 'none', cursor: 'pointer',
                background: days === opt.value ? 'var(--accent)' : 'transparent',
                color: days === opt.value ? '#fff' : 'var(--muted)',
                transition: 'all .15s',
              }}
            >{opt.label}</button>
          ))}
        </div>

        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} /> Refresh
        </button>
      </div>

      {/* No pipeline configured */}
      {pipelines.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--muted)' }}>
          No pipelines configured yet.
        </div>
      )}

      {pipelines.length > 0 && (
        <>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 10, marginBottom: 20 }}>
            <StatCard icon={Activity} label="Total Sessions" value={summary?.total ?? '—'} />
            <StatCard
              icon={CheckCircle2} label="Success Rate"
              value={summary ? `${summary.success_rate}%` : '—'}
              sub={summary ? `${summary.succeeded} ok / ${summary.failed} fail` : undefined}
              color={summary?.success_rate >= 80 ? 'var(--green)' : 'var(--red)'}
            />
            <StatCard icon={DollarSign} label="Total Cost" value={fmtCost(summary?.total_cost_usd)} />
            <StatCard icon={DollarSign} label="Avg Cost / Session" value={fmtCost(summary?.avg_cost_usd)} color="var(--muted)" />
            <StatCard icon={Clock} label="Avg Duration" value={fmtDur(summary?.avg_duration_s)} color="var(--muted)" />
          </div>

          {/* Breakdown */}
          {(stageItems.length > 0 || modelItems.length > 0) && (
            <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
              <BreakdownList title="By Stage" items={stageItems} maxCost={maxStageCost} />
              <BreakdownList title="By Model" items={modelItems} maxCost={maxModelCost} />
            </div>
          )}

          {/* Session log */}
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)', marginBottom: 10 }}>
            Session Log
          </div>

          {loading && sessions.length === 0 ? (
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', color: 'var(--muted)', padding: 32 }}>
              <div className="spinner" /> Loading…
            </div>
          ) : sessions.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--muted)' }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📊</div>
              No sessions in this time range.<br />
              <span style={{ fontSize: 12 }}>Start a pipeline run to see Copilot sessions appear here.</span>
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {['Issue', 'Title', 'Stage', 'Status', 'Started', 'Duration', 'Model', 'Cost'].map(h => (
                      <th key={h} style={{
                        padding: '9px 12px', textAlign: 'left', color: 'var(--muted)',
                        fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em',
                        whiteSpace: 'nowrap', background: 'var(--card)',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s: any, i: number) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}
                      title={s.error_message ? `Error: ${s.error_message}` : undefined}>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', whiteSpace: 'nowrap', color: 'var(--muted)' }}>
                        {s.issue_repo ? `${s.issue_repo.split('/').pop()}#${s.issue_number}` : `#${s.issue_number ?? '—'}`}
                      </td>
                      <td style={{ padding: '8px 12px', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {s.issue_title || '—'}
                      </td>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                        <span style={{
                          background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                          color: 'var(--accent)', borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 600,
                        }}>{s.stage || '—'}</span>
                      </td>
                      <td style={{ padding: '8px 12px', whiteSpace: 'nowrap' }}>
                        <StatusBadge status={s.status || 'unknown'} />
                      </td>
                      <td style={{ padding: '8px 12px', color: 'var(--muted)', whiteSpace: 'nowrap' }}
                        title={s.started_at || undefined}>
                        {fmtTime(s.started_at)}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                        {fmtDur(s.duration_s)}
                      </td>
                      <td style={{ padding: '8px 12px', color: 'var(--muted)', fontSize: 11, whiteSpace: 'nowrap' }}>
                        {s.model || '—'}
                      </td>
                      <td style={{ padding: '8px 12px', fontFamily: 'monospace', whiteSpace: 'nowrap',
                        color: s.cost_usd ? 'var(--fg)' : 'var(--muted)' }}>
                        {fmtCost(s.cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
