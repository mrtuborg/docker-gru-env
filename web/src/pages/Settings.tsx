import { useEffect, useState } from 'react'

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>(null)

  useEffect(() => {
    fetch('/api/settings').then(r => r.json()).then(setSettings).catch(() => {})
  }, [])

  return (
    <div>
      <h1 style={{ fontSize:22, fontWeight:700, marginBottom:24 }}>Settings</h1>
      {settings && (
        <div className="card" style={{ maxWidth:480 }}>
          <div className="section-label">Server</div>
          <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
            <div>
              <div className="form-label">Data Directory</div>
              <input className="form-input" readOnly value={settings.data_dir || ''} />
            </div>
            <div>
              <div className="form-label">Port</div>
              <input className="form-input" readOnly value={settings.server_port || '9400'} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
