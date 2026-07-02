import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { RefreshCw, Activity, Plug, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import HealthBadge from '../components/HealthBadge'

function OverallHealthBanner({ status }: { status: string }) {
  const cfg = {
    healthy:  { bg:'color-mix(in srgb, var(--green) 12%, transparent)', border:'var(--green)', icon: <CheckCircle2 size={16} color="var(--green)"/>, text:'All systems operational' },
    degraded: { bg:'color-mix(in srgb, var(--yellow) 12%, transparent)', border:'var(--yellow)', icon: <AlertTriangle size={16} color="var(--yellow)"/>, text:'Some connectors need attention' },
    error:    { bg:'color-mix(in srgb, var(--red) 12%, transparent)', border:'var(--red)', icon: <AlertTriangle size={16} color="var(--red)"/>, text:'One or more connectors are failing' },
    unknown:  { bg:'var(--surface2)', border:'var(--border)', icon: <Loader2 size={16} color="var(--muted)"/>, text:'Checking connector health…' },
  }[status] ?? { bg:'var(--surface2)', border:'var(--border)', icon: null, text: status }

  return (
    <div style={{ display:'flex', alignItems:'center', gap:10, padding:'12px 16px', borderRadius:8, border:`1px solid ${cfg.border}`, background:cfg.bg, marginBottom:24 }}>
      {cfg.icon}
      <span style={{ fontSize:13, fontWeight:500 }}>{cfg.text}</span>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [sessions, setSessions] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const location = useLocation()
  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    Promise.all([
      fetch('/api/dashboard').then(r => r.json()),
      fetch('/api/sessions').then(r => r.json()).catch(() => []),
    ]).then(([dash, sess]) => {
      setData(dash)
      setSessions(Array.isArray(sess) ? sess.slice(0, 5) : [])
    }).finally(() => setLoading(false))
  }

  // Reload whenever we navigate to the dashboard (e.g. after auth callback)
  useEffect(() => { load() }, [location.key])

  if (loading && !data) return (
    <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)', padding:48 }}>
      <div className="spinner"/> Loading dashboard…
    </div>
  )

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:20 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Dashboard</h1>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }}/> Refresh
        </button>
      </div>

      {data && <OverallHealthBanner status={data.overall_health} />}

      {data?.needs_setup && (
        <div className="card" style={{ borderColor:'var(--accent)', marginBottom:24 }}>
          <div style={{ fontWeight:600, marginBottom:6 }}>🧪 No connectors connected yet</div>
          <div style={{ color:'var(--muted)', marginBottom:14, fontSize:13 }}>
            Connect your first connector to start watching project boards and running Copilot sessions.
          </div>
          <a href="/wizard" className="btn btn-primary" style={{ display:'inline-flex' }}>Start Setup Wizard →</a>
        </div>
      )}

      {/* Connector health grid */}
      {data?.connectors?.length > 0 && (
        <section style={{ marginBottom:32 }}>
          <div className="section-label" style={{ display:'flex', alignItems:'center', gap:6, marginBottom:12 }}>
            <Plug size={13}/>Connectors
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))', gap:12 }}>
            {data.connectors.map((p: any) => (
              <div
                key={p.id}
                className="card"
                onClick={() => navigate('/connectors')}
                style={{ padding:'16px 16px 12px', cursor:'pointer' }}
                title="Go to Connectors"
              >
                <div style={{ fontSize:22, marginBottom:6 }}>
                  {p.plugin_type === 'github' ? '🐙' : p.plugin_type === 'copilot' ? '🤖' : p.plugin_type === 'azure' ? '☁️' : p.plugin_type === 'obsidian' ? '🔮' : p.plugin_type === 'analytics' ? '🗄️' : '📝'}
                </div>
                <div style={{ fontWeight:600, fontSize:13, marginBottom:6 }}>{p.display_name}</div>
                <HealthBadge status={p.health?.status} message={p.health?.message}/>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Recent sessions */}
      <section>
        <div className="section-label" style={{ display:'flex', alignItems:'center', gap:6, marginBottom:12 }}>
          <Activity size={13}/>Recent Sessions
        </div>
        {sessions.length === 0 ? (
          <div className="card" style={{ color:'var(--muted)', textAlign:'center', padding:'28px 16px', fontSize:13 }}>
            No sessions recorded yet. Sessions will appear here once Copilot runs start.
          </div>
        ) : (
          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            {sessions.map((s: any, i: number) => (
              <div key={i} className="card" style={{ display:'flex', alignItems:'center', gap:12, padding:'12px 16px' }}>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:500, fontSize:13, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{s.issue || s.id || 'Session'}</div>
                  <div style={{ fontSize:11, color:'var(--muted)' }}>{s.started_at ? new Date(s.started_at).toLocaleString() : ''}</div>
                </div>
                {s.status && <span className={`badge badge-${s.status === 'done' ? 'green' : s.status === 'error' ? 'red' : 'info'}`}>{s.status}</span>}
                {s.cost_usd != null && <span style={{ fontSize:11, color:'var(--muted)', whiteSpace:'nowrap' }}>${Number(s.cost_usd).toFixed(4)}</span>}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
