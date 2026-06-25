/**
 * AuthCallback — handles OAuth redirect callbacks.
 *
 * After GitHub App Manifest Flow completes, GitHub redirects to:
 *   /#/auth-callback?plugin=<id>&status=app_registered&app_name=<name>&app_id=<id>&host=<host>
 *
 * Flow:
 *   1. Show "App Created" confirmation
 *   2. Show instructions to enable Device Flow in GitHub App settings
 *   3. User clicks "I've enabled it" → start Device Flow
 *   4. Done
 */
import { useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { CheckCircle2, AlertCircle, ExternalLink } from 'lucide-react'
import OAuthModal from '../components/OAuthModal'

export default function AuthCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()

  const pluginId = params.get('plugin')
  const status = params.get('status')
  const appName = params.get('app_name')
  const host = params.get('host') || 'github.com'
  const errorMsg = params.get('message')

  const [phase, setPhase] = useState<'registered' | 'enable_device_flow' | 'device_flow' | 'done' | 'error'>(
    status === 'error' ? 'error' : 'registered'
  )

  const appSlug = (appName || 'gru-server').toLowerCase().replace(/[\s()]/g, '-').replace(/-+/g, '-')
  const appSettingsUrl = `https://${host}/settings/apps/${appSlug}`

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
      <div className="modal-card" style={{ maxWidth: 520 }}>

        {phase === 'registered' && (
          <div style={{ textAlign:'center' }}>
            <CheckCircle2 size={48} color="var(--green)" style={{ marginBottom:16 }}/>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>GitHub App Created!</h2>
            <p style={{ color:'var(--muted)', marginBottom:20, lineHeight:1.6 }}>
              <strong>{appName || 'Gru Server'}</strong> has been registered on {host}.
            </p>
            <button className="btn btn-primary" onClick={() => setPhase('enable_device_flow')}>
              Continue →
            </button>
          </div>
        )}

        {phase === 'enable_device_flow' && (
          <div>
            <h2 style={{ fontSize:18, fontWeight:700, marginBottom:16 }}>One More Step Required</h2>
            <p style={{ color:'var(--muted)', marginBottom:12, lineHeight:1.6 }}>
              You need to enable <strong>Device Flow</strong> in the GitHub App settings before signing in.
            </p>
            <ol style={{ color:'var(--muted)', fontSize:14, lineHeight:2.2, paddingLeft:20, marginBottom:20 }}>
              <li>Click the button below to open the GitHub App settings</li>
              <li>Scroll down to <strong>"Enable Device Flow"</strong></li>
              <li>Check the box and click <strong>"Save changes"</strong></li>
              <li>Come back here and click <strong>"I've enabled it"</strong></li>
            </ol>
            <a
              href={appSettingsUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display:'inline-flex', alignItems:'center', gap:6, padding:'10px 16px',
                background:'var(--surface2)', border:'1px solid var(--border)', borderRadius:6,
                fontSize:13, fontWeight:500, color:'var(--text)', textDecoration:'none',
                marginBottom:20 }}>
              <ExternalLink size={14}/> Open GitHub App Settings
            </a>
            <div>
              <button className="btn btn-primary" onClick={() => setPhase('device_flow')} style={{ width:'100%' }}>
                ✓ I've enabled Device Flow — Sign In
              </button>
            </div>
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
