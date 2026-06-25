/**
 * AuthCallback — handles OAuth redirect callbacks.
 *
 * After GitHub App Manifest Flow completes, GitHub redirects to:
 *   /#/auth-callback?plugin=<id>&status=app_registered&app_name=<name>
 *
 * This page then automatically starts the Device Flow to get a user token.
 */
import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle2, Loader2, AlertCircle } from 'lucide-react'
import OAuthModal from '../components/OAuthModal'

export default function AuthCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()

  const pluginId = params.get('plugin')
  const status = params.get('status')
  const appName = params.get('app_name')
  const errorMsg = params.get('message')

  const [phase, setPhase] = useState<'registered' | 'device_flow' | 'done' | 'error'>(
    status === 'error' ? 'error' : 'registered'
  )

  // Auto-transition: after showing "app registered" → start device flow
  useEffect(() => {
    if (phase === 'registered' && pluginId) {
      const t = setTimeout(() => setPhase('device_flow'), 2000)
      return () => clearTimeout(t)
    }
  }, [phase, pluginId])

  if (!pluginId) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', minHeight:'60vh' }}>
        <div className="modal-card" style={{ maxWidth: 420, textAlign:'center' }}>
          <AlertCircle size={36} color="var(--red)" style={{ marginBottom:12 }}/>
          <h2 style={{ fontSize:18, fontWeight:700, marginBottom:8 }}>Missing Parameters</h2>
          <p style={{ color:'var(--muted)', marginBottom:20 }}>No plugin ID in callback URL.</p>
          <button className="btn btn-primary" onClick={() => navigate('/')}>Go to Dashboard</button>
        </div>
      </div>
    )
  }

  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', minHeight:'60vh' }}>
      <div className="modal-card" style={{ maxWidth: 480 }}>

        {phase === 'registered' && (
          <div style={{ textAlign:'center' }}>
            <CheckCircle2 size={48} color="var(--green)" style={{ marginBottom:16 }}/>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>GitHub App Created!</h2>
            <p style={{ color:'var(--muted)', marginBottom:8, lineHeight:1.6 }}>
              <strong>{appName || 'Gru Server'}</strong> has been registered successfully.
            </p>
            <p style={{ color:'var(--muted)', fontSize:13 }}>
              <Loader2 size={14} className="spin" style={{ marginRight:6, verticalAlign:'middle' }}/>
              Starting authorization…
            </p>
          </div>
        )}

        {phase === 'device_flow' && (
          <OAuthModal
            pluginId={pluginId}
            onClose={() => setPhase('done')}
          />
        )}

        {phase === 'done' && (
          <div style={{ textAlign:'center' }}>
            <CheckCircle2 size={48} color="var(--green)" style={{ marginBottom:16 }}/>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>Authorization Complete!</h2>
            <p style={{ color:'var(--muted)', marginBottom:20 }}>Your GitHub plugin is fully configured.</p>
            <button className="btn btn-primary" onClick={() => navigate('/')}>Go to Dashboard</button>
          </div>
        )}

        {phase === 'error' && (
          <div style={{ textAlign:'center' }}>
            <AlertCircle size={48} color="var(--red)" style={{ marginBottom:16 }}/>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>Registration Failed</h2>
            <p style={{ color:'var(--muted)', marginBottom:20 }}>{errorMsg || 'Unknown error occurred.'}</p>
            <button className="btn btn-primary" onClick={() => navigate('/plugins')}>Go to Plugins</button>
          </div>
        )}

      </div>
    </div>
  )
}
