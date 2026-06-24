import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import HealthBadge from '../components/HealthBadge'

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    fetch('/api/dashboard')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:24 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Dashboard</h1>
        <button className="btn btn-ghost" onClick={load} title="Refresh">
          <RefreshCw size={14} className={loading ? 'pulse' : ''} /> Refresh
        </button>
      </div>

      {loading && !data && (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)' }}>
          <div className="spinner"/> Loading…
        </div>
      )}

      {data && (
        <>
          {/* Plugin health row */}
          <div className="section-label">Plugins</div>
          {data.plugins?.length === 0 ? (
            <div className="card" style={{ color:'var(--muted)', textAlign:'center', padding:32 }}>
              No plugins connected. <a href="/wizard">Run the setup wizard →</a>
            </div>
          ) : (
            <div style={{ display:'flex', gap:12, flexWrap:'wrap', marginBottom:24 }}>
              {data.plugins?.map((p: any) => (
                <div key={p.id} className="card" style={{ minWidth:140, flex:'0 1 auto' }}>
                  <div style={{ fontSize:20, marginBottom:6 }}>
                    {p.plugin_type === 'github' ? '🐙' : p.plugin_type === 'copilot' ? '🤖' : p.plugin_type === 'azure' ? '☁️' : '📝'}
                  </div>
                  <div style={{ fontWeight:600, fontSize:13, marginBottom:4 }}>{p.display_name}</div>
                  <HealthBadge status={p.health?.status} message={p.health?.message}/>
                </div>
              ))}
            </div>
          )}

          {data.needs_setup && (
            <div className="card" style={{ borderColor:'var(--accent)', marginBottom:24 }}>
              <div style={{ fontWeight:600, marginBottom:8 }}>🧪 Welcome to Gru's Lab!</div>
              <div style={{ color:'var(--muted)', marginBottom:16 }}>
                Connect your first plugin to start watching project boards and running Copilot sessions.
              </div>
              <a href="/wizard" className="btn btn-primary">Start Setup Wizard →</a>
            </div>
          )}
        </>
      )}
    </div>
  )
}
