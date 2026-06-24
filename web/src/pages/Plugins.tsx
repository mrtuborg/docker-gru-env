import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GitBranch, Bot, Cloud, FileText, Plus, RefreshCw, Pencil, Trash2, X, Save, Loader2 } from 'lucide-react'
import HealthBadge from '../components/HealthBadge'
import OAuthModal from '../components/OAuthModal'
import PluginConfigForm from '../components/PluginConfigForm'

const TYPE_META: Record<string, { icon: any; color: string; label: string }> = {
  github:   { icon: GitBranch, color:'#58a6ff', label:'GitHub' },
  copilot:  { icon: Bot,       color:'#3fb950', label:'GitHub Copilot' },
  azure:    { icon: Cloud,     color:'#79c0ff', label:'Azure Storage' },
  obsidian: { icon: FileText,  color:'#bc8cff', label:'Obsidian Kanban' },
}

const PLUGIN_TYPES = Object.entries(TYPE_META).map(([id, m]) => ({ id, ...m }))

function AddPluginModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [typeId, setTypeId] = useState<string | null>(null)
  const [pluginId, setPluginId] = useState('')
  const [config, setConfig] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    if (!typeId || !pluginId) { setError('Plugin ID is required'); return }
    setSaving(true); setError(null)
    const { token, client_secret, ...rest } = config as any
    try {
      const r = await fetch('/api/plugins', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: pluginId, plugin_type: typeId, config: rest }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Save failed') }
      if (token) {
        await fetch(`/api/plugins/${pluginId}/credentials`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ type:'pat', token }) })
      }
      onSaved()
    } catch(e: any) { setError(e.message) }
    finally { setSaving(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-card" style={{ maxWidth:520, width:'90%', maxHeight:'85vh', overflow:'auto' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:20 }}>
          <h2 style={{ fontSize:17, fontWeight:700 }}>Add Plugin</h2>
          <button className="btn btn-ghost" style={{ padding:'4px 6px' }} onClick={onClose}><X size={16}/></button>
        </div>

        {!typeId ? (
          <div>
            <p style={{ color:'var(--muted)', fontSize:13, marginBottom:16 }}>Select plugin type:</p>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:10 }}>
              {PLUGIN_TYPES.map(({ id, icon: Icon, color, label }) => (
                <div key={id} className="card card-interactive" onClick={() => { setTypeId(id); setPluginId(`${id}-${Date.now().toString(36)}`) }}
                  style={{ cursor:'pointer', display:'flex', alignItems:'center', gap:12, padding:'12px 14px' }}>
                  <div style={{ width:32, height:32, borderRadius:6, background:`color-mix(in srgb, ${color} 15%, transparent)`, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>
                    <Icon size={16} color={color}/>
                  </div>
                  <span style={{ fontWeight:500, fontSize:13 }}>{label}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div>
            <div style={{ marginBottom:16 }}>
              <div className="form-label">Plugin ID <span style={{ color:'var(--red)' }}>*</span></div>
              <input className="form-input" value={pluginId} onChange={e => setPluginId(e.target.value)} placeholder="e.g. github-main"/>
              <div style={{ fontSize:11, color:'var(--muted)', marginTop:3 }}>Unique identifier for this plugin instance</div>
            </div>
            <PluginConfigForm pluginType={typeId} onChange={setConfig}/>
            {error && <div style={{ color:'var(--red)', fontSize:12, marginTop:12 }}>⚠ {error}</div>}
            <div style={{ display:'flex', gap:8, justifyContent:'flex-end', marginTop:20 }}>
              <button className="btn btn-ghost" onClick={() => setTypeId(null)}>← Change type</button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? <><Loader2 size={13} className="spin"/>Saving…</> : <><Save size={13}/>Save Plugin</>}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function EditPluginModal({ plugin, onClose, onSaved }: { plugin: any; onClose: () => void; onSaved: () => void }) {
  const [config, setConfig] = useState<Record<string, any>>(plugin.config || {})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const meta = TYPE_META[plugin.plugin_type]

  const save = async () => {
    setSaving(true); setError(null)
    const { token, client_secret, ...rest } = config as any
    try {
      const r = await fetch(`/api/plugins/${plugin.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ config: rest }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Save failed') }
      if (token) {
        await fetch(`/api/plugins/${plugin.id}/credentials`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ type:'pat', token }) })
      }
      onSaved()
    } catch(e: any) { setError(e.message) }
    finally { setSaving(false) }
  }

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-card" style={{ maxWidth:520, width:'90%', maxHeight:'85vh', overflow:'auto' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:20 }}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {meta && <div style={{ width:32, height:32, borderRadius:6, background:`color-mix(in srgb, ${meta.color} 15%, transparent)`, display:'flex', alignItems:'center', justifyContent:'center' }}>
              <meta.icon size={16} color={meta.color}/>
            </div>}
            <h2 style={{ fontSize:17, fontWeight:700 }}>{plugin.display_name}</h2>
          </div>
          <button className="btn btn-ghost" style={{ padding:'4px 6px' }} onClick={onClose}><X size={16}/></button>
        </div>
        <PluginConfigForm pluginType={plugin.plugin_type} initialValues={config} onChange={setConfig}/>
        {error && <div style={{ color:'var(--red)', fontSize:12, marginTop:12 }}>⚠ {error}</div>}
        <div style={{ display:'flex', gap:8, justifyContent:'flex-end', marginTop:20 }}>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? <><Loader2 size={13} className="spin"/>Saving…</> : <><Save size={13}/>Save Changes</>}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Plugins() {
  const navigate = useNavigate()
  const [plugins, setPlugins] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [oauthPlugin, setOauthPlugin] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [editPlugin, setEditPlugin] = useState<any | null>(null)

  const load = () => {
    setLoading(true)
    fetch('/api/plugins').then(r => r.json()).then(d => { setPlugins(d); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const refreshHealth = async (id: string) => {
    const r = await fetch(`/api/plugins/${id}/health`)
    const h = await r.json()
    setPlugins(ps => ps.map(p => p.id === id ? { ...p, health: h } : p))
  }

  const disconnect = async (id: string) => {
    if (!confirm(`Disconnect plugin '${id}'? This cannot be undone.`)) return
    await fetch(`/api/plugins/${id}`, { method:'DELETE' })
    setPlugins(ps => ps.filter(p => p.id !== id))
  }

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:24 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Plugins</h1>
        <div style={{ display:'flex', gap:8 }}>
          <button className="btn btn-ghost" onClick={load}><RefreshCw size={14}/> Refresh</button>
          <button className="btn btn-primary" onClick={() => setAddOpen(true)}><Plus size={14}/> Add Plugin</button>
        </div>
      </div>

      {loading && plugins.length === 0 && (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)' }}><div className="spinner"/> Loading…</div>
      )}

      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))', gap:16 }}>
        {plugins.map((p: any) => {
          const meta = TYPE_META[p.plugin_type] || { icon: Plus, color:'var(--muted)', label: p.plugin_type }
          const Icon = meta.icon
          return (
            <div key={p.id} className="card">
              <div style={{ display:'flex', alignItems:'flex-start', gap:12, marginBottom:12 }}>
                <div style={{ width:40, height:40, borderRadius:8, flexShrink:0, background:`color-mix(in srgb, ${meta.color} 15%, transparent)`, display:'flex', alignItems:'center', justifyContent:'center' }}>
                  <Icon size={20} color={meta.color}/>
                </div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:600, marginBottom:2, fontSize:14 }}>{p.display_name}</div>
                  <div style={{ fontSize:11, color:'var(--muted)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{p.id}</div>
                </div>
              </div>

              <HealthBadge status={p.health?.status} message={p.health?.message}/>

              <div style={{ display:'flex', gap:6, marginTop:12, flexWrap:'wrap' }}>
                <button className="btn btn-ghost" style={{ fontSize:11, padding:'4px 10px' }} onClick={() => refreshHealth(p.id)}>
                  <RefreshCw size={11}/> Health
                </button>
                <button className="btn btn-secondary" style={{ fontSize:11, padding:'4px 10px' }} onClick={() => setEditPlugin(p)}>
                  <Pencil size={11}/> Configure
                </button>
                {p.plugin_type === 'github' && (
                  <button className="btn btn-secondary" style={{ fontSize:11, padding:'4px 10px' }} onClick={() => setOauthPlugin(p.id)}>
                    🔗 Authorize
                  </button>
                )}
                <button className="btn btn-danger" style={{ fontSize:11, padding:'4px 10px' }} onClick={() => disconnect(p.id)}>
                  <Trash2 size={11}/> Remove
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {plugins.length === 0 && !loading && (
        <div className="card" style={{ textAlign:'center', padding:48, color:'var(--muted)' }}>
          <div style={{ fontSize:32, marginBottom:12 }}>🔌</div>
          No plugins connected yet.{' '}
          <button className="btn btn-primary" style={{ marginTop:12, display:'block', margin:'12px auto 0' }} onClick={() => navigate('/wizard')}>
            Run Setup Wizard
          </button>
        </div>
      )}

      {addOpen && <AddPluginModal onClose={() => setAddOpen(false)} onSaved={() => { setAddOpen(false); load() }}/>}
      {editPlugin && <EditPluginModal plugin={editPlugin} onClose={() => setEditPlugin(null)} onSaved={() => { setEditPlugin(null); load() }}/>}
      {oauthPlugin && <OAuthModal pluginId={oauthPlugin} onClose={() => { setOauthPlugin(null); load() }}/>}
    </div>
  )
}
