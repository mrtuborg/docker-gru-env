import { useEffect, useState } from 'react'
import { GitBranch, Bot, Cloud, FileText, Plus, RefreshCw } from 'lucide-react'
import HealthBadge from '../components/HealthBadge'
import OAuthModal from '../components/OAuthModal'

const TYPE_META: Record<string, { icon: any; color: string; label: string }> = {
  github:   { icon: GitBranch,   color:'#58a6ff', label:'GitHub' },
  copilot:  { icon: Bot,      color:'#3fb950', label:'GitHub Copilot' },
  azure:    { icon: Cloud,    color:'#79c0ff', label:'Azure Storage' },
  obsidian: { icon: FileText, color:'#bc8cff', label:'Obsidian Kanban' },
}

export default function Plugins() {
  const [plugins, setPlugins] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [oauthPlugin, setOauthPlugin] = useState<string | null>(null)

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
    if (!confirm(`Disconnect plugin '${id}'?`)) return
    await fetch(`/api/plugins/${id}`, { method:'DELETE' })
    setPlugins(ps => ps.filter(p => p.id !== id))
  }

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:24 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Plugins</h1>
        <button className="btn btn-secondary" onClick={load}>
          <RefreshCw size={14}/> Refresh
        </button>
      </div>

      {loading && plugins.length === 0 && (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)' }}>
          <div className="spinner"/> Loading…
        </div>
      )}

      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))', gap:16 }}>
        {plugins.map((p: any) => {
          const meta = TYPE_META[p.plugin_type] || { icon: Plus, color:'var(--muted)', label: p.plugin_type }
          const Icon = meta.icon
          return (
            <div key={p.id} className="card">
              <div style={{ display:'flex', alignItems:'flex-start', gap:12, marginBottom:12 }}>
                <div style={{
                  width:40, height:40, borderRadius:8, flexShrink:0,
                  background:`color-mix(in srgb, ${meta.color} 15%, transparent)`,
                  display:'flex', alignItems:'center', justifyContent:'center',
                }}>
                  <Icon size={20} color={meta.color}/>
                </div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:600, marginBottom:2 }}>{p.display_name}</div>
                  <div style={{ fontSize:11, color:'var(--muted)' }}>{p.description}</div>
                </div>
              </div>

              <HealthBadge status={p.health?.status} message={p.health?.message}/>

              <div style={{ display:'flex', gap:8, marginTop:12, flexWrap:'wrap' }}>
                <button className="btn btn-ghost" style={{ fontSize:11, padding:'4px 10px' }}
                  onClick={() => refreshHealth(p.id)}>
                  <RefreshCw size={11}/> Check health
                </button>
                {p.plugin_type === 'github' && (
                  <button className="btn btn-secondary" style={{ fontSize:11, padding:'4px 10px' }}
                    onClick={() => setOauthPlugin(p.id)}>
                    🔗 Authorize
                  </button>
                )}
                <button className="btn btn-danger" style={{ fontSize:11, padding:'4px 10px' }}
                  onClick={() => disconnect(p.id)}>
                  Disconnect
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {plugins.length === 0 && !loading && (
        <div className="card" style={{ textAlign:'center', padding:48, color:'var(--muted)' }}>
          No plugins connected yet. <a href="/wizard">Run the setup wizard →</a>
        </div>
      )}

      {oauthPlugin && (
        <OAuthModal
          pluginId={oauthPlugin}
          onClose={() => { setOauthPlugin(null); load() }}
        />
      )}
    </div>
  )
}
