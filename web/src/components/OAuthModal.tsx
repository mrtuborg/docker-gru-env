import { useEffect, useRef, useState } from 'react'
import { Copy, Check, X } from 'lucide-react'

interface OAuthModalProps { pluginId: string; onClose: () => void }

export default function OAuthModal({ pluginId, onClose }: OAuthModalProps) {
  const [flowData, setFlowData] = useState<any>(null)
  const [status, setStatus] = useState<'loading'|'waiting'|'success'|'error'>('loading')
  const [message, setMessage] = useState('')
  const [copiedUrl, setCopiedUrl] = useState(false)
  const [copiedCode, setCopiedCode] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    fetch(`/api/plugins/${pluginId}/auth/device/start`, { method:'POST' })
      .then(async r => {
        const d = await r.json()
        if (!r.ok) {
          setStatus('error')
          setMessage(d.detail || 'Failed to start device flow')
          return
        }
        setFlowData(d)
        setStatus('waiting')
        setCountdown(d.expires_in || 900)
        // Start polling
        pollRef.current = setInterval(async () => {
          const r = await fetch(`/api/plugins/${pluginId}/auth/device/poll`, { method:'POST' })
          const result = await r.json()
          if (result.granted) {
            clearInterval(pollRef.current!)
            setStatus('success')
            setTimeout(onClose, 1500)
          } else if (!r.ok) {
            clearInterval(pollRef.current!)
            setStatus('error')
            setMessage(result.detail || 'Authorization failed')
          }
        }, (d.interval || 5) * 1000)
      })
      .catch(e => { setStatus('error'); setMessage(String(e)) })
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [pluginId])

  // Countdown timer
  useEffect(() => {
    if (status !== 'waiting' || countdown <= 0) return
    const t = setTimeout(() => setCountdown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [countdown, status])

  const copy = (text: string, setFlag: (v:boolean) => void) => {
    navigator.clipboard.writeText(text)
    setFlag(true)
    setTimeout(() => setFlag(false), 2000)
  }

  const mins = Math.floor(countdown / 60).toString().padStart(2,'0')
  const secs = (countdown % 60).toString().padStart(2,'0')

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal-card">
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:20 }}>
          <div style={{ fontWeight:700, fontSize:16 }}>🔗 Authorize Plugin</div>
          <button className="btn btn-ghost" style={{ padding:'4px 8px' }} onClick={onClose}><X size={16}/></button>
        </div>

        {status === 'loading' && (
          <div style={{ display:'flex', alignItems:'center', gap:12, color:'var(--muted)' }}>
            <div className="spinner"/> Starting device flow…
          </div>
        )}

        {status === 'waiting' && flowData && (
          <>
            <div style={{ marginBottom:16 }}>
              <div className="form-label">1. Open this URL in your browser</div>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <input className="form-input" readOnly value={flowData.verification_uri || ''} style={{ fontFamily:'monospace', fontSize:12 }}/>
                <button className="btn btn-ghost" style={{ flexShrink:0, padding:'8px' }}
                  onClick={() => copy(flowData.verification_uri, setCopiedUrl)}>
                  {copiedUrl ? <Check size={14} color="var(--green)"/> : <Copy size={14}/>}
                </button>
              </div>
            </div>
            <div style={{ marginBottom:20 }}>
              <div className="form-label">2. Enter this code</div>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <div className="oauth-code" style={{ flex:1 }}>{flowData.user_code}</div>
                <button className="btn btn-ghost" style={{ flexShrink:0, padding:'8px' }}
                  onClick={() => copy(flowData.user_code, setCopiedCode)}>
                  {copiedCode ? <Check size={14} color="var(--green)"/> : <Copy size={14}/>}
                </button>
              </div>
            </div>
            <div style={{ display:'flex', alignItems:'center', gap:10, color:'var(--muted)', fontSize:13 }}>
              <div className="spinner"/>
              <span>Waiting for authorization…</span>
              <span style={{ marginLeft:'auto', fontFamily:'monospace' }}>Expires in {mins}:{secs}</span>
            </div>
          </>
        )}

        {status === 'success' && (
          <div style={{ textAlign:'center', padding:16 }}>
            <div style={{ fontSize:36, marginBottom:8 }}>✓</div>
            <div style={{ color:'var(--green)', fontWeight:600 }}>Authorization successful!</div>
          </div>
        )}

        {status === 'error' && (
          <div style={{ textAlign:'center', padding:16 }}>
            <div style={{ fontSize:36, marginBottom:8 }}>✗</div>
            <div style={{ color:'var(--red)', marginBottom:16 }}>{message || 'Authorization failed'}</div>
            <button className="btn btn-secondary" onClick={onClose}>Close</button>
          </div>
        )}
      </div>
    </div>
  )
}
