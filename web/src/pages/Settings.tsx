import { useEffect, useRef, useState } from 'react'
import { Save, Upload, Download, Loader2, AlertTriangle } from 'lucide-react'
import ConnectorConfigForm from '../components/ConnectorConfigForm'

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const [importYaml, setImportYaml] = useState('')
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [dockerConfig, setDockerConfig] = useState<Record<string, any>>({})
  const [dockerSaving, setDockerSaving] = useState(false)
  const [dockerMsg, setDockerMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetch('/api/settings').then(r => r.json()).then(d => {
      setSettings(d)
      // Extract docker settings from stored settings
      setDockerConfig({
        cw_image: d.cw_image || 'gru:local',
        cw_data_volume: d.cw_data_volume || 'gru-data',
        cw_logs_volume: d.cw_logs_volume || 'gru-logs',
        cw_instruct_volume: d.cw_instruct_volume || 'gru-instructions',
        cw_ssh_path: d.cw_ssh_path || '~/.ssh',
      })
    }).catch(() => {})
  }, [])

  const saveDockerSettings = async () => {
    setDockerSaving(true); setDockerMsg(null)
    try {
      await fetch('/api/settings', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ ...settings, ...dockerConfig }) })
      setDockerMsg('Saved ✓'); setTimeout(() => setDockerMsg(null), 2500)
    } catch { setDockerMsg('Error saving') }
    finally { setDockerSaving(false) }
  }

  const saveSettings = async () => {
    setSaving(true); setSaveMsg(null)
    try {
      await fetch('/api/settings', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(settings) })
      setSaveMsg('Saved ✓')
      setTimeout(() => setSaveMsg(null), 2500)
    } catch { setSaveMsg('Error saving') }
    finally { setSaving(false) }
  }

  const exportYaml = async () => {
    const r = await fetch('/api/settings/export')
    const text = await r.text()
    const blob = new Blob([text], { type:'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = 'config.yml'; a.click()
    URL.revokeObjectURL(url)
  }

  const importFromYaml = async () => {
    if (!importYaml.trim()) return
    setImporting(true); setImportMsg(null)
    try {
      const r = await fetch('/api/settings/import', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ yaml: importYaml }) })
      const d = await r.json()
      if (!r.ok) throw new Error(d.detail || 'Import failed')
      setImportMsg({ ok: true, text: `Imported: ${d.connectors_created} connector(s) created` })
      setImportYaml('')
    } catch(e: any) {
      setImportMsg({ ok: false, text: e.message })
    } finally { setImporting(false) }
  }

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = ev => setImportYaml(ev.target?.result as string)
    reader.readAsText(f)
  }

  return (
    <div style={{ maxWidth:640 }}>
      <h1 style={{ fontSize:22, fontWeight:700, marginBottom:24 }}>Settings</h1>

      {/* Server settings */}
      <section className="card" style={{ marginBottom:20 }}>
        <div className="section-label" style={{ marginBottom:16 }}>Server</div>
        {settings && (
          <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
            <div>
              <div className="form-label">Data Directory</div>
              <input className="form-input" value={settings.data_dir || ''} readOnly style={{ opacity:0.7 }}/>
              <div style={{ fontSize:11, color:'var(--muted)', marginTop:3 }}>Set via GRU_DATA_DIR env var or --data-dir flag</div>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12 }}>
              <div>
                <div className="form-label">Port</div>
                <input className="form-input" type="number" value={settings.server_port || 9400}
                  onChange={e => setSettings((s: any) => ({ ...s, server_port: Number(e.target.value) }))}/>
              </div>
              <div>
                <div className="form-label">Health Check Interval (s)</div>
                <input className="form-input" type="number" value={settings.health_check_interval || 30}
                  onChange={e => setSettings((s: any) => ({ ...s, health_check_interval: Number(e.target.value) }))}/>
              </div>
            </div>
            <div style={{ display:'flex', alignItems:'center', gap:10 }}>
              <button className="btn btn-primary" onClick={saveSettings} disabled={saving}>
                {saving ? <><Loader2 size={13} className="spin"/>Saving…</> : <><Save size={13}/>Save</>}
              </button>
              {saveMsg && <span style={{ fontSize:12, color: saveMsg.startsWith('Error') ? 'var(--red)' : 'var(--green)' }}>{saveMsg}</span>}
            </div>
          </div>
        )}
      </section>

      {/* Export config */}
      <section className="card" style={{ marginBottom:20 }}>
        <div className="section-label" style={{ marginBottom:8 }}>Export Configuration</div>
        <p style={{ color:'var(--muted)', fontSize:13, marginBottom:14 }}>
          Download current connector configuration as a <code style={{ fontFamily:'monospace', fontSize:11, background:'var(--surface2)', padding:'1px 5px', borderRadius:3 }}>config.yml</code> file compatible with the CLI mode.
        </p>
        <button className="btn btn-secondary" onClick={exportYaml}>
          <Download size={13}/> Download config.yml
        </button>
      </section>

      {/* Import config */}
      <section className="card" style={{ marginBottom:20 }}>
        <div className="section-label" style={{ marginBottom:8 }}>Import from config.yml</div>
        <p style={{ color:'var(--muted)', fontSize:13, marginBottom:14 }}>
          Paste an existing <code style={{ fontFamily:'monospace', fontSize:11, background:'var(--surface2)', padding:'1px 5px', borderRadius:3 }}>.gru/config.yml</code> to create connectors from it.
        </p>
        <input type="file" accept=".yml,.yaml" ref={fileRef} onChange={handleFileUpload} style={{ display:'none' }}/>
        <button className="btn btn-ghost" style={{ marginBottom:10, fontSize:12 }} onClick={() => fileRef.current?.click()}>
          <Upload size={13}/> Load from file
        </button>
        <textarea
          className="form-input"
          value={importYaml}
          onChange={e => setImportYaml(e.target.value)}
          placeholder="Paste YAML here or load from file above…"
          style={{ fontFamily:'monospace', fontSize:12, minHeight:120, resize:'vertical', display:'block', width:'100%', boxSizing:'border-box' }}
        />
        {importMsg && (
          <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, marginTop:8, color: importMsg.ok ? 'var(--green)' : 'var(--red)' }}>
            {!importMsg.ok && <AlertTriangle size={12}/>}
            {importMsg.text}
          </div>
        )}
        <button className="btn btn-primary" style={{ marginTop:12 }} onClick={importFromYaml} disabled={!importYaml.trim() || importing}>
          {importing ? <><Loader2 size={13} className="spin"/>Importing…</> : <><Upload size={13}/>Import</>}
        </button>
      </section>

      {/* Docker infrastructure */}
      <section className="card" style={{ marginBottom:20 }}>
        <div className="section-label" style={{ marginBottom:4 }}>Docker Infrastructure</div>
        <p style={{ color:'var(--muted)', fontSize:13, marginBottom:16 }}>
          Controls how Copilot sessions are run inside Docker containers.
        </p>
        <ConnectorConfigForm connectorType="docker" initialValues={dockerConfig} onChange={setDockerConfig}/>
        <div style={{ display:'flex', alignItems:'center', gap:10, marginTop:16 }}>
          <button className="btn btn-primary" onClick={saveDockerSettings} disabled={dockerSaving}>
            {dockerSaving ? <><Loader2 size={13} className="spin"/>Saving…</> : <><Save size={13}/>Save</>}
          </button>
          {dockerMsg && <span style={{ fontSize:12, color: dockerMsg.startsWith('Error') ? 'var(--red)' : 'var(--green)' }}>{dockerMsg}</span>}
        </div>
      </section>

      {/* Danger zone */}
      <section className="card" style={{ borderColor:'var(--red)' }}>
        <div className="section-label" style={{ marginBottom:8, color:'var(--red)' }}>Danger Zone</div>
        <p style={{ color:'var(--muted)', fontSize:13, marginBottom:14 }}>
          Reset the setup wizard. This does <strong>not</strong> delete connectors — only marks setup as incomplete so the wizard runs again on next visit.
        </p>
        <button className="btn btn-danger" onClick={async () => {
          if (!confirm('Reset wizard state?')) return
          await fetch('/api/settings', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ wizard_complete: false }) })
          window.location.href = '/wizard'
        }}>
          Reset Wizard
        </button>
      </section>
    </div>
  )
}
