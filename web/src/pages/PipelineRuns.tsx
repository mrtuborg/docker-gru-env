import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, RefreshCw, CheckCircle2, XCircle, Clock, AlertTriangle } from 'lucide-react'

interface Run {
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
}

interface RunItem {
  issue_number: number
  issue_repo: string
  stage: string
  status: string
  started_at: string | null
  ended_at: string | null
  duration_s: number | null
  model: string | null
  cost_usd: number | null
  error_message: string | null
}

const statusIcon = (s: string) => {
  switch (s) {
    case 'success': case 'completed': return <CheckCircle2 size={14} color="var(--green)"/>
    case 'failure': case 'failed': return <XCircle size={14} color="var(--red)"/>
    case 'timeout': return <Clock size={14} color="var(--yellow)"/>
    case 'running': return <div className="spinner" style={{ width:14, height:14 }}/>
    default: return <AlertTriangle size={14} color="var(--muted)"/>
  }
}

export default function PipelineRuns() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<Run[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const [runItems, setRunItems] = useState<Record<string, RunItem[]>>({})

  const load = () => {
    setLoading(true)
    fetch(`/api/pipelines/${id}/runs`).then(r => r.json())
      .then(setRuns)
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [id])

  const toggleRun = async (runId: string) => {
    if (expandedRun === runId) {
      setExpandedRun(null)
      return
    }
    setExpandedRun(runId)
    if (!runItems[runId]) {
      const items = await fetch(`/api/pipelines/${id}/runs/${runId}/items`).then(r => r.json())
      setRunItems(prev => ({ ...prev, [runId]: items }))
    }
  }

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:20 }}>
        <button className="btn btn-ghost" onClick={() => navigate(`/pipelines/${id}`)} style={{ padding:'6px 8px' }}>
          <ArrowLeft size={16}/>
        </button>
        <h1 style={{ fontSize:18, fontWeight:700, flex:1 }}>Run History</h1>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }}/> Refresh
        </button>
      </div>

      {loading && runs.length === 0 ? (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)', padding:48 }}>
          <div className="spinner"/> Loading runs…
        </div>
      ) : runs.length === 0 ? (
        <div className="card" style={{ textAlign:'center', padding:'48px 24px', color:'var(--muted)' }}>
          No runs recorded yet. Start the pipeline to see run history here.
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
          {runs.map(run => (
            <div key={run.id} className="card" style={{ padding:0, overflow:'hidden' }}>
              <div
                className="card-interactive"
                onClick={() => toggleRun(run.id)}
                style={{ display:'flex', alignItems:'center', gap:12, padding:'12px 16px', cursor:'pointer' }}
              >
                {statusIcon(run.status)}
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:13, fontWeight:500 }}>
                    {new Date(run.started_at).toLocaleString()}
                    {run.ended_at && (
                      <span style={{ color:'var(--muted)', fontWeight:400 }}>
                        {' '}— {Math.round((new Date(run.ended_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s
                      </span>
                    )}
                  </div>
                </div>
                <div style={{ display:'flex', gap:8, fontSize:11 }}>
                  {run.issues_succeeded > 0 && <span style={{ color:'var(--green)' }}>✓{run.issues_succeeded}</span>}
                  {run.issues_failed > 0 && <span style={{ color:'var(--red)' }}>✕{run.issues_failed}</span>}
                  {run.issues_skipped > 0 && <span style={{ color:'var(--muted)' }}>⊘{run.issues_skipped}</span>}
                </div>
                <span className={`badge badge-${run.status === 'completed' ? 'healthy' : run.status === 'failed' ? 'error' : run.status === 'running' ? 'info' : 'unknown'}`}>
                  {run.status}
                </span>
              </div>

              {/* Expanded items */}
              {expandedRun === run.id && (
                <div style={{ borderTop:'1px solid var(--border)', padding:'0' }}>
                  {!runItems[run.id] ? (
                    <div style={{ padding:16, display:'flex', gap:8, alignItems:'center', color:'var(--muted)' }}>
                      <div className="spinner"/> Loading…
                    </div>
                  ) : runItems[run.id].length === 0 ? (
                    <div style={{ padding:16, color:'var(--muted)', fontSize:12 }}>No items in this run.</div>
                  ) : (
                    <table style={{ width:'100%', fontSize:12, borderCollapse:'collapse' }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'8px 12px', textAlign:'left', color:'var(--muted)', fontWeight:600 }}>Issue</th>
                          <th style={{ padding:'8px 12px', textAlign:'left', color:'var(--muted)', fontWeight:600 }}>Stage</th>
                          <th style={{ padding:'8px 12px', textAlign:'left', color:'var(--muted)', fontWeight:600 }}>Status</th>
                          <th style={{ padding:'8px 12px', textAlign:'right', color:'var(--muted)', fontWeight:600 }}>Duration</th>
                          <th style={{ padding:'8px 12px', textAlign:'right', color:'var(--muted)', fontWeight:600 }}>Cost</th>
                          <th style={{ padding:'8px 12px', textAlign:'left', color:'var(--muted)', fontWeight:600 }}>Model</th>
                        </tr>
                      </thead>
                      <tbody>
                        {runItems[run.id].map((item, i) => (
                          <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                            <td style={{ padding:'8px 12px' }}>
                              <span style={{ fontFamily:'monospace' }}>{item.issue_repo}#{item.issue_number}</span>
                            </td>
                            <td style={{ padding:'8px 12px' }}>{item.stage}</td>
                            <td style={{ padding:'8px 12px' }}>
                              <div style={{ display:'flex', alignItems:'center', gap:4 }}>
                                {statusIcon(item.status)} {item.status}
                              </div>
                              {item.error_message && (
                                <div style={{ color:'var(--red)', fontSize:11, marginTop:2 }}>{item.error_message}</div>
                              )}
                            </td>
                            <td style={{ padding:'8px 12px', textAlign:'right', fontFamily:'monospace' }}>
                              {item.duration_s != null ? `${item.duration_s.toFixed(0)}s` : '—'}
                            </td>
                            <td style={{ padding:'8px 12px', textAlign:'right', fontFamily:'monospace' }}>
                              {item.cost_usd != null ? `$${item.cost_usd.toFixed(4)}` : '—'}
                            </td>
                            <td style={{ padding:'8px 12px', fontSize:11, color:'var(--muted)' }}>
                              {item.model || '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
