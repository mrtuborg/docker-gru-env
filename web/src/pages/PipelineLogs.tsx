import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Trash2, AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'

interface LogEvent {
  level: string
  message: string
  pipeline_id: string
  issue: number
  stage: string
  timestamp: string
}

const levelIcon = (level: string) => {
  switch (level) {
    case 'success': return <CheckCircle2 size={13} color="var(--green)"/>
    case 'error': return <XCircle size={13} color="var(--red)"/>
    case 'warn': return <AlertTriangle size={13} color="var(--yellow)"/>
    default: return <Info size={13} color="var(--accent)"/>
  }
}

const levelColor = (level: string) => {
  switch (level) {
    case 'success': return 'var(--green)'
    case 'error': return 'var(--red)'
    case 'warn': return 'var(--yellow)'
    default: return 'var(--text)'
  }
}

export default function PipelineLogs() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [logs, setLogs] = useState<LogEvent[]>([])
  const [connected, setConnected] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const es = new EventSource(`/api/pipelines/${id}/logs`)
    esRef.current = es

    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)

    const handler = (e: MessageEvent) => {
      try {
        const event = JSON.parse(e.data) as LogEvent
        setLogs(prev => [...prev.slice(-499), event])
      } catch {}
    }

    // Listen to all event types
    for (const type of ['info', 'warn', 'error', 'success']) {
      es.addEventListener(type, handler)
    }

    return () => {
      es.close()
      esRef.current = null
    }
  }, [id])

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'calc(100vh - 56px - 48px)' }}>
      {/* Header */}
      <div style={{
        display:'flex', alignItems:'center', gap:12,
        padding:'0 0 16px', borderBottom:'1px solid var(--border)', marginBottom:0,
      }}>
        <button className="btn btn-ghost" onClick={() => navigate(`/pipelines/${id}`)} style={{ padding:'6px 8px' }}>
          <ArrowLeft size={16}/>
        </button>
        <h1 style={{ fontSize:18, fontWeight:700, flex:1 }}>Live Logs</h1>
        <div style={{ display:'flex', alignItems:'center', gap:6 }}>
          <div className={`dot ${connected ? 'dot-green' : 'dot-red'}`}/>
          <span style={{ fontSize:11, color:'var(--muted)' }}>{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
        <button className="btn btn-ghost" style={{ fontSize:11 }} onClick={() => setLogs([])}>
          <Trash2 size={12}/> Clear
        </button>
      </div>

      {/* Log stream */}
      <div style={{
        flex:1, overflow:'auto', padding:'8px 0',
        fontFamily:'ui-monospace, "SFMono-Regular", Menlo, monospace',
        fontSize:12, lineHeight:1.7,
      }}>
        {logs.length === 0 ? (
          <div style={{ color:'var(--muted)', padding:'48px 16px', textAlign:'center' }}>
            {connected ? 'Waiting for events… Start the pipeline to see logs here.' : 'Connecting…'}
          </div>
        ) : (
          logs.map((log, i) => (
            <div key={i} style={{
              display:'flex', alignItems:'flex-start', gap:8, padding:'2px 8px',
              borderBottom:'1px solid color-mix(in srgb, var(--border) 30%, transparent)',
            }}>
              <span style={{ color:'var(--muted)', fontSize:10, minWidth:65, flexShrink:0, paddingTop:2 }}>
                {new Date(log.timestamp).toLocaleTimeString()}
              </span>
              {levelIcon(log.level)}
              <span style={{ color: levelColor(log.level), flex:1 }}>
                {log.issue > 0 && (
                  <span style={{ color:'var(--accent)', marginRight:6 }}>#{log.issue}</span>
                )}
                {log.stage && (
                  <span style={{
                    fontSize:10, padding:'1px 5px', borderRadius:3,
                    background:'var(--surface2)', border:'1px solid var(--border)',
                    marginRight:6,
                  }}>{log.stage}</span>
                )}
                {log.message}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef}/>
      </div>
    </div>
  )
}
