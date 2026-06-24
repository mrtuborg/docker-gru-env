import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Play, Pause, FileText, RefreshCw, Bot, User } from 'lucide-react'

interface Pipeline {
  id: string
  name: string
  enabled: number
  plugin_id: string
  board_type: string
  project_owner: string | null
  project_number: number | null
  poll_interval: number
  stages: Stage[]
  models: { model: string; priority: number }[]
  findings: { project_owner: string; project_number: number; initial_status: string } | null
}

interface Stage {
  column_name: string
  actor: string
  stage_index: number
}

export default function Pipelines() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const load = () => {
    setLoading(true)
    fetch('/api/pipelines').then(r => r.json())
      .then(setPipelines)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const togglePipeline = async (p: Pipeline) => {
    const endpoint = p.enabled ? 'stop' : 'start'
    await fetch(`/api/pipelines/${p.id}/${endpoint}`, { method: 'POST' })
    await fetch(`/api/pipelines/${p.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !p.enabled }),
    })
    load()
  }

  if (loading && pipelines.length === 0) return (
    <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)', padding:48 }}>
      <div className="spinner"/> Loading pipelines…
    </div>
  )

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:20 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Pipelines</h1>
        <div style={{ display:'flex', gap:8 }}>
          <button className="btn btn-ghost" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }}/> Refresh
          </button>
          <button className="btn btn-primary" onClick={() => navigate('/pipelines/new')}>
            <Plus size={14}/> New Pipeline
          </button>
        </div>
      </div>

      {pipelines.length === 0 ? (
        <div className="card" style={{ textAlign:'center', padding:'48px 24px' }}>
          <div style={{ fontSize:48, marginBottom:16 }}>🔬</div>
          <div style={{ fontWeight:600, fontSize:15, marginBottom:8 }}>No pipelines configured</div>
          <div style={{ color:'var(--muted)', fontSize:13, marginBottom:20, maxWidth:400, margin:'0 auto 20px' }}>
            Pipelines connect a project board to AI agents. Each board column becomes a stage where an AI agent or human reviews the work.
          </div>
          <button className="btn btn-primary" onClick={() => navigate('/pipelines/new')}>
            <Plus size={14}/> Create your first pipeline
          </button>
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {pipelines.map(p => (
            <div key={p.id} className="card" style={{ padding:0, overflow:'hidden' }}>
              {/* Header */}
              <div style={{ display:'flex', alignItems:'center', gap:12, padding:'16px 20px', borderBottom:'1px solid var(--border)' }}>
                <span style={{ fontSize:20 }}>🔬</span>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:600, fontSize:14 }}>{p.name}</div>
                  <div style={{ fontSize:11, color:'var(--muted)', marginTop:2 }}>
                    {p.plugin_id} · project #{p.project_number}
                    {p.findings && ` → findings → #${p.findings.project_number}`}
                  </div>
                </div>
                <span className={`badge ${p.enabled ? 'badge-healthy' : 'badge-unknown'}`}>
                  {p.enabled ? '● Running' : '⏸ Paused'}
                </span>
              </div>

              {/* Stage chips */}
              <div style={{ padding:'12px 20px', display:'flex', alignItems:'center', gap:4, flexWrap:'wrap' }}>
                {p.stages.map((s, i) => (
                  <div key={s.column_name} style={{ display:'flex', alignItems:'center', gap:4 }}>
                    {i > 0 && <span style={{ color:'var(--muted)', fontSize:11 }}>→</span>}
                    <span style={{
                      display:'inline-flex', alignItems:'center', gap:4,
                      padding:'3px 10px', borderRadius:12,
                      fontSize:11, fontWeight:500,
                      background: s.actor === 'ai' ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'var(--surface2)',
                      color: s.actor === 'ai' ? 'var(--accent)' : 'var(--muted)',
                      border: '1px solid ' + (s.actor === 'ai' ? 'color-mix(in srgb, var(--accent) 25%, transparent)' : 'var(--border)'),
                    }}>
                      {s.actor === 'ai' ? <Bot size={10}/> : <User size={10}/>}
                      {s.column_name}
                    </span>
                  </div>
                ))}
              </div>

              {/* Actions */}
              <div style={{ display:'flex', justifyContent:'flex-end', gap:8, padding:'0 20px 14px' }}>
                <button className="btn btn-secondary" style={{ padding:'5px 12px', fontSize:12 }}
                  onClick={() => navigate(`/pipelines/${p.id}`)}>
                  Edit
                </button>
                <button
                  className={`btn ${p.enabled ? 'btn-secondary' : 'btn-primary'}`}
                  style={{ padding:'5px 12px', fontSize:12 }}
                  onClick={() => togglePipeline(p)}
                >
                  {p.enabled ? <><Pause size={12}/> Pause</> : <><Play size={12}/> Start</>}
                </button>
                <button className="btn btn-secondary" style={{ padding:'5px 12px', fontSize:12 }}
                  onClick={() => navigate(`/pipelines/${p.id}/runs`)}>
                  <FileText size={12}/> Runs
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
