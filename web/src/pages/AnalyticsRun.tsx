import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, RefreshCw, CheckCircle2, XCircle, Clock, AlertTriangle, DollarSign, Zap, Layers } from 'lucide-react'

interface RunMeta {
  id: string
  pipeline_id: string
  started_at: string
  ended_at: string | null
  status: string
  issues_processed: number
  issues_succeeded: number
  issues_failed: number
  issues_skipped: number
  model_used: string | null
  duration_s: number
}

interface RunItem {
  issue_number: number
  issue_repo: string
  issue_title: string | null
  stage: string
  status: string
  started_at: string | null
  ended_at: string | null
  duration_s: number | null
  model: string | null
  cost_usd: number | null
  session_id: string | null
  error_message: string | null
  tokens_input: number | null
  tokens_output: number | null
  tokens_cache_read: number | null
  tokens_reasoning: number | null
  nano_aiu: number | null
  premium_requests: number | null
  api_requests: number | null
  lines_added: number | null
  lines_removed: number | null
}

interface RunDetail {
  run: RunMeta
  items: RunItem[]
}

function StatCard({ icon, label, value }: { icon: any; label: string; value: string | number }) {
  const Icon = icon
  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 18px' }}>
      <div style={{ width: 38, height: 38, borderRadius: 8, background: 'color-mix(in srgb, var(--accent) 15%, transparent)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon size={18} color="var(--accent)" />
      </div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1 }}>{value}</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>{label}</div>
      </div>
    </div>
  )
}

const statusIcon = (s: string) => {
  switch (s) {
    case 'success': case 'completed': return <CheckCircle2 size={14} color="var(--green)" />
    case 'failure': case 'failed': return <XCircle size={14} color="var(--red)" />
    case 'timeout': return <Clock size={14} color="var(--yellow)" />
    case 'running': return <div className="spinner" style={{ width: 14, height: 14 }} />
    default: return <AlertTriangle size={14} color="var(--muted)" />
  }
}

const statusBadge = (s: string) => {
  const cls = s === 'completed' ? 'healthy' : s === 'failed' ? 'error' : s === 'running' ? 'info' : 'unknown'
  return <span className={`badge badge-${cls}`}>{s}</span>
}

/** Simple timeline: one bar per item, height ~ duration, colored by status. */
function ItemTimeline({ items }: { items: RunItem[] }) {
  const sorted = items.filter(i => i.started_at).slice().sort((a, b) => (a.started_at! < b.started_at! ? -1 : 1))
  if (sorted.length === 0) return <div style={{ color: 'var(--muted)', fontSize: 12, padding: 16 }}>No timed items.</div>
  const maxDur = Math.max(...sorted.map(i => i.duration_s || 0), 1)
  const H = 140
  const colorFor = (s: string) => s === 'success' || s === 'completed' ? 'var(--green)' : s === 'failure' || s === 'failed' ? 'var(--red)' : 'var(--muted)'

  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: H, padding: '8px 4px', overflowX: 'auto' }}>
      {sorted.map((it, i) => {
        const h = Math.max(4, ((it.duration_s || 0) / maxDur) * (H - 24))
        return (
          <div key={i} title={`${it.issue_repo}#${it.issue_number} · ${it.stage} · ${it.status} · ${it.duration_s?.toFixed(0) || 0}s`}
            style={{ width: 18, height: h, borderRadius: 3, background: colorFor(it.status), opacity: 0.85, flexShrink: 0 }} />
        )
      })}
    </div>
  )
}

export default function AnalyticsRun() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const [data, setData] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch(`/api/analytics/runs/${runId}`)
      .then(async r => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}))
          throw new Error(body.detail || `HTTP ${r.status}`)
        }
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [runId])

  const totalCost = data?.items.reduce((s, i) => s + (i.cost_usd || 0), 0) || 0
  const totalTokens = data?.items.reduce((s, i) => s + (i.tokens_input || 0) + (i.tokens_output || 0), 0) || 0

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <button className="btn btn-ghost" onClick={() => navigate('/analytics')} style={{ padding: '6px 8px' }}>
          <ArrowLeft size={16} />
        </button>
        <h1 style={{ fontSize: 18, fontWeight: 700, flex: 1, fontFamily: 'monospace' }}>{runId}</h1>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} /> Refresh
        </button>
      </div>

      {loading && !data ? (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', color: 'var(--muted)', padding: 48 }}>
          <div className="spinner" /> Loading run…
        </div>
      ) : error ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--red)' }}>
          {error}
        </div>
      ) : data && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, fontSize: 12, color: 'var(--muted)' }}>
            <span>{data.run.pipeline_id}</span>
            <span>·</span>
            <span>{new Date(data.run.started_at).toLocaleString()}</span>
            {statusBadge(data.run.status)}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(160px,1fr))', gap: 12, marginBottom: 28 }}>
            <StatCard icon={Layers} label="Issues Processed" value={data.run.issues_processed} />
            <StatCard icon={CheckCircle2} label="Succeeded" value={data.run.issues_succeeded} />
            <StatCard icon={XCircle} label="Failed" value={data.run.issues_failed} />
            <StatCard icon={Clock} label="Duration" value={`${data.run.duration_s.toFixed(0)}s`} />
            <StatCard icon={DollarSign} label="Est. Cost" value={`$${totalCost.toFixed(4)}`} />
            <StatCard icon={Zap} label="Tokens" value={totalTokens >= 1e6 ? `${(totalTokens / 1e6).toFixed(1)}M` : totalTokens >= 1e3 ? `${(totalTokens / 1e3).toFixed(1)}K` : totalTokens} />
          </div>

          <section style={{ marginBottom: 28 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Item Timeline
            </h2>
            <div className="card">
              <ItemTimeline items={data.items} />
            </div>
          </section>

          <section>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Run Items ({data.items.length})
            </h2>
            {data.items.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--muted)' }}>
                No items recorded for this run.
              </div>
            ) : (
              <div className="card" style={{ padding: 0, overflow: 'hidden', overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Issue</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Stage</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Status</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--muted)', fontWeight: 600 }}>Duration</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--muted)', fontWeight: 600 }}>Cost</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Model</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--muted)', fontWeight: 600 }}>Tokens In/Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 12px' }}>
                          <span style={{ fontFamily: 'monospace' }}>{item.issue_repo}#{item.issue_number}</span>
                          {item.issue_title && <div style={{ color: 'var(--muted)', fontSize: 11 }}>{item.issue_title}</div>}
                        </td>
                        <td style={{ padding: '8px 12px' }}>{item.stage}</td>
                        <td style={{ padding: '8px 12px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            {statusIcon(item.status)} {item.status}
                          </div>
                          {item.error_message && (
                            <div style={{ color: 'var(--red)', fontSize: 11, marginTop: 2 }}>{item.error_message}</div>
                          )}
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: 'monospace' }}>
                          {item.duration_s != null ? `${item.duration_s.toFixed(0)}s` : '—'}
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: 'monospace' }}>
                          {item.cost_usd != null ? `$${item.cost_usd.toFixed(4)}` : '—'}
                        </td>
                        <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--muted)' }}>
                          {item.model || '—'}
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: 'monospace', fontSize: 11, color: 'var(--muted)' }}>
                          {item.tokens_input != null || item.tokens_output != null
                            ? `${item.tokens_input ?? 0} / ${item.tokens_output ?? 0}`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
