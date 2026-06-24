import { useEffect, useState } from 'react'
import { RefreshCw, DollarSign, Clock, Activity } from 'lucide-react'

function StatCard({ icon, label, value, sub }: { icon: any; label: string; value: string | number; sub?: string }) {
  const Icon = icon
  return (
    <div className="card" style={{ display:'flex', alignItems:'center', gap:14, padding:'16px 18px' }}>
      <div style={{ width:38, height:38, borderRadius:8, background:'color-mix(in srgb, var(--accent) 15%, transparent)', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>
        <Icon size={18} color="var(--accent)"/>
      </div>
      <div>
        <div style={{ fontSize:20, fontWeight:700, lineHeight:1 }}>{value}</div>
        <div style={{ fontSize:12, color:'var(--muted)', marginTop:3 }}>{label}</div>
        {sub && <div style={{ fontSize:11, color:'var(--muted)', marginTop:1 }}>{sub}</div>}
      </div>
    </div>
  )
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<any[]>([])
  const [cost, setCost] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    Promise.all([
      fetch('/api/sessions').then(r => r.json()).catch(() => []),
      fetch('/api/sessions/cost/report').then(r => r.json()).catch(() => null),
    ]).then(([sess, costData]) => {
      setSessions(Array.isArray(sess) ? sess : [])
      setCost(costData)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const totalCost = cost?.total_usd ?? sessions.reduce((sum: number, s: any) => sum + (s.cost_usd || 0), 0)
  const totalSessions = sessions.length

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:24 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Sessions & Cost</h1>
        <button className="btn btn-ghost" onClick={load}><RefreshCw size={14}/> Refresh</button>
      </div>

      {/* Stats row */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))', gap:12, marginBottom:28 }}>
        <StatCard icon={Activity} label="Total Sessions" value={totalSessions}/>
        <StatCard icon={DollarSign} label="Total Cost" value={`$${Number(totalCost).toFixed(4)}`}/>
        {cost?.by_model && Object.entries(cost.by_model).slice(0,1).map(([model, data]: [string, any]) => (
          <StatCard key={model} icon={Clock} label="Top Model" value={model.split('-').slice(-2).join('-')} sub={`$${Number(data.cost_usd||0).toFixed(4)}`}/>
        ))}
      </div>

      {/* Session table */}
      <div className="section-label" style={{ marginBottom:12 }}>Session Log</div>

      {loading && sessions.length === 0 ? (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)' }}><div className="spinner"/> Loading…</div>
      ) : sessions.length === 0 ? (
        <div className="card" style={{ color:'var(--muted)', textAlign:'center', padding:48 }}>
          <div style={{ fontSize:32, marginBottom:12 }}>📊</div>
          No sessions recorded yet.<br/>
          <span style={{ fontSize:13 }}>Sessions appear here once Copilot runs complete and push logs to the data repo.</span>
        </div>
      ) : (
        <div style={{ overflow:'auto' }}>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
            <thead>
              <tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Issue / ID','Started','Duration','Model','Cost','Status'].map(h => (
                  <th key={h} style={{ textAlign:'left', padding:'6px 10px', color:'var(--muted)', fontWeight:600, fontSize:11, textTransform:'uppercase', letterSpacing:'0.05em', whiteSpace:'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map((s: any, i: number) => (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'10px 10px', maxWidth:220, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    <span title={s.issue || s.id}>{s.issue || s.id || '—'}</span>
                  </td>
                  <td style={{ padding:'10px', color:'var(--muted)', whiteSpace:'nowrap' }}>
                    {s.started_at ? new Date(s.started_at).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding:'10px', color:'var(--muted)', whiteSpace:'nowrap' }}>
                    {s.duration_s != null ? `${Math.round(s.duration_s)}s` : '—'}
                  </td>
                  <td style={{ padding:'10px', color:'var(--muted)', whiteSpace:'nowrap', fontSize:11 }}>
                    {s.model || '—'}
                  </td>
                  <td style={{ padding:'10px', whiteSpace:'nowrap' }}>
                    {s.cost_usd != null ? `$${Number(s.cost_usd).toFixed(4)}` : '—'}
                  </td>
                  <td style={{ padding:'10px' }}>
                    <span className={`badge badge-${s.status === 'done' ? 'green' : s.status === 'error' ? 'red' : 'info'}`}>
                      {s.status || 'unknown'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
