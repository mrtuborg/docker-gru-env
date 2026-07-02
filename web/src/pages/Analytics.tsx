import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, DollarSign, Activity, CheckCircle2, Layers, Zap } from 'lucide-react'

interface RunRow {
  id: string
  pipeline_id: string
  started_at: string
  ended_at: string | null
  status: string
  issues_processed: number
  issues_succeeded: number
  issues_failed: number
  model_used: string | null
  duration_s: number
}

interface PipelineAgg {
  pipeline_id: string
  runs: number
  items: number
  succeeded: number
  cost_usd: number
}

interface DailyPoint {
  day: string
  items: number
  cost_usd: number
}

interface Overview {
  summary: {
    total_runs: number
    total_items: number
    items_succeeded: number
    items_failed: number
    success_rate: number
    total_cost_usd: number
    total_tokens_input: number
    total_tokens_output: number
    avg_duration_s: number
  }
  by_pipeline: PipelineAgg[]
  runs: RunRow[]
  daily: DailyPoint[]
}

function StatCard({ icon, label, value, sub }: { icon: any; label: string; value: string | number; sub?: string }) {
  const Icon = icon
  return (
    <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 18px' }}>
      <div style={{ width: 38, height: 38, borderRadius: 8, background: 'color-mix(in srgb, var(--accent) 15%, transparent)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon size={18} color="var(--accent)" />
      </div>
      <div>
        <div style={{ fontSize: 20, fontWeight: 700, lineHeight: 1 }}>{value}</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>{label}</div>
        {sub && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>{sub}</div>}
      </div>
    </div>
  )
}

function fmtTokens(v: number) {
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return String(v)
}

/** Combo bar (cost) + line (items) chart per pipeline, matching the reference cost dashboard style. */
function PipelineComboChart({ data }: { data: PipelineAgg[] }) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(entries => setWidth(entries[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  if (!data.length) return <div style={{ color: 'var(--muted)', fontSize: 12, padding: 24 }}>No pipeline data yet.</div>

  const H = 220
  const PAD = { t: 20, b: 40, l: 56, r: 52 }
  const cW = Math.max(100, width - PAD.l - PAD.r)
  const cH = H - PAD.t - PAD.b
  const n = data.length
  const bw = Math.min(48, (cW / n) * 0.5)
  const maxCost = Math.max(...data.map(d => d.cost_usd), 0.01)
  const maxItems = Math.max(...data.map(d => d.items), 1)

  const barX = (i: number) => PAD.l + (i + 0.5) * (cW / n) - bw / 2
  const lineX = (i: number) => PAD.l + (i + 0.5) * (cW / n)
  const lineY = (v: number) => PAD.t + cH - (v / maxItems) * cH

  const linePoints = data.map((d, i) => `${lineX(i)},${lineY(d.items)}`).join(' ')

  return (
    <div ref={ref} style={{ width: '100%' }}>
      <svg width={width} height={H} style={{ display: 'block' }}>
        {/* gridlines */}
        {[0, 0.25, 0.5, 0.75, 1].map(f => (
          <line key={f} x1={PAD.l} x2={width - PAD.r} y1={PAD.t + cH * (1 - f)} y2={PAD.t + cH * (1 - f)} stroke="var(--border)" strokeDasharray="3,3" />
        ))}
        {/* bars: cost */}
        {data.map((d, i) => {
          const h = (d.cost_usd / maxCost) * cH
          return (
            <rect key={d.pipeline_id} x={barX(i)} y={PAD.t + cH - h} width={bw} height={Math.max(1, h)} rx={3}
              fill="var(--accent)" opacity={0.55} />
          )
        })}
        {/* line: items */}
        <polyline points={linePoints} fill="none" stroke="var(--green)" strokeWidth={2} />
        {data.map((d, i) => (
          <circle key={d.pipeline_id} cx={lineX(i)} cy={lineY(d.items)} r={3.5} fill="var(--green)" />
        ))}
        {/* x labels */}
        {data.map((d, i) => (
          <text key={d.pipeline_id} x={lineX(i)} y={H - 14} textAnchor="middle" fontSize={11} fill="var(--muted)">
            {d.pipeline_id}
          </text>
        ))}
        {/* y axis labels */}
        <text x={PAD.l - 8} y={PAD.t + 4} textAnchor="end" fontSize={10} fill="var(--muted)">${maxCost.toFixed(2)}</text>
        <text x={width - PAD.r + 8} y={PAD.t + 4} fontSize={10} fill="var(--muted)">{maxItems}</text>
      </svg>
      <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--muted)', paddingLeft: PAD.l }}>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, background: 'var(--accent)', opacity: 0.55, borderRadius: 2, marginRight: 4 }} />Cost (USD)</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, background: 'var(--green)', borderRadius: '50%', marginRight: 4 }} />Items</span>
      </div>
    </div>
  )
}

/** GitHub-style daily activity heatmap. */
function ActivityHeatmap({ daily }: { daily: DailyPoint[] }) {
  const byDay = new Map(daily.map(d => [d.day, d.items]))
  const today = new Date()
  const days: { date: string; items: number }[] = []
  for (let i = 89; i >= 0; i--) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    days.push({ date: key, items: byDay.get(key) || 0 })
  }
  const max = Math.max(...days.map(d => d.items), 1)
  const level = (v: number) => v === 0 ? 0 : v / max > 0.66 ? 3 : v / max > 0.33 ? 2 : 1
  const colors = ['var(--surface2)', 'color-mix(in srgb, var(--green) 35%, var(--surface2))', 'color-mix(in srgb, var(--green) 65%, var(--surface2))', 'var(--green)']

  const weeks: { date: string; items: number }[][] = []
  let cur: { date: string; items: number }[] = []
  days.forEach((d, i) => {
    cur.push(d)
    if (cur.length === 7 || i === days.length - 1) { weeks.push(cur); cur = [] }
  })

  return (
    <div style={{ display: 'flex', gap: 3, overflowX: 'auto', padding: '4px 0' }}>
      {weeks.map((week, wi) => (
        <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {week.map(d => (
            <div key={d.date} title={`${d.date}: ${d.items} item(s)`}
              style={{ width: 11, height: 11, borderRadius: 2, background: colors[level(d.items)] }} />
          ))}
        </div>
      ))}
    </div>
  )
}

const statusBadge = (s: string) => {
  const cls = s === 'completed' ? 'healthy' : s === 'failed' ? 'error' : s === 'running' ? 'info' : 'unknown'
  return <span className={`badge badge-${cls}`}>{s}</span>
}

export default function Analytics() {
  const navigate = useNavigate()
  const [data, setData] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch('/api/analytics/overview')
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

  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Analytics</h1>
        <button className="btn btn-ghost" onClick={load}><RefreshCw size={14} /> Refresh</button>
      </div>

      {loading && !data ? (
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', color: 'var(--muted)', padding: 48 }}>
          <div className="spinner" /> Loading analytics…
        </div>
      ) : error ? (
        <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--red)' }}>
          {error}
        </div>
      ) : data && (
        <>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: 12, marginBottom: 28 }}>
            <StatCard icon={Layers} label="Pipeline Runs" value={data.summary.total_runs} />
            <StatCard icon={Activity} label="Items (Sessions)" value={data.summary.total_items}
              sub={`${data.summary.items_succeeded} succeeded · ${data.summary.items_failed} failed`} />
            <StatCard icon={CheckCircle2} label="Success Rate" value={`${data.summary.success_rate}%`} />
            <StatCard icon={DollarSign} label="Est. Cost (USD)" value={`$${data.summary.total_cost_usd.toFixed(2)}`} />
            <StatCard icon={Zap} label="Tokens (in+out)" value={fmtTokens(data.summary.total_tokens_input + data.summary.total_tokens_output)} />
          </div>

          {/* Activity heatmap */}
          <section style={{ marginBottom: 28 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Activity — Last 90 Days
            </h2>
            <div className="card">
              <ActivityHeatmap daily={data.daily} />
            </div>
          </section>

          {/* Cost & items by pipeline */}
          <section style={{ marginBottom: 28 }}>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Cost &amp; Items by Pipeline
            </h2>
            <div className="card">
              <PipelineComboChart data={data.by_pipeline} />
            </div>
          </section>

          {/* Recent runs table */}
          <section>
            <h2 style={{ fontSize: 13, fontWeight: 600, color: 'var(--muted)', marginBottom: 12, paddingBottom: 8, borderBottom: '1px solid var(--border)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Recent Pipeline Runs
            </h2>
            {data.runs.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--muted)' }}>
                No pipeline runs recorded yet.
              </div>
            ) : (
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Run</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Pipeline</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Started</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--muted)', fontWeight: 600 }}>Duration</th>
                      <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--muted)', fontWeight: 600 }}>Issues</th>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: 'var(--muted)', fontWeight: 600 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.runs.map(run => (
                      <tr key={run.id} className="card-interactive" onClick={() => navigate(`/analytics/run/${run.id}`)}
                        style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}>
                        <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{run.id}</td>
                        <td style={{ padding: '8px 12px' }}>{run.pipeline_id}</td>
                        <td style={{ padding: '8px 12px', color: 'var(--muted)' }}>{new Date(run.started_at).toLocaleString()}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: 'monospace' }}>{run.duration_s ? `${run.duration_s.toFixed(0)}s` : '—'}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                          {run.issues_succeeded > 0 && <span style={{ color: 'var(--green)' }}>✓{run.issues_succeeded}</span>}
                          {run.issues_failed > 0 && <span style={{ color: 'var(--red)', marginLeft: 6 }}>✕{run.issues_failed}</span>}
                        </td>
                        <td style={{ padding: '8px 12px' }}>{statusBadge(run.status)}</td>
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
