import { useState } from 'react'
import { CheckCircle2, GitBranch, Bot, Cloud, FileText, ChevronRight, ChevronLeft, Loader2 } from 'lucide-react'
import PluginConfigForm from '../components/PluginConfigForm'

const PLUGIN_TYPES = [
  { id:'github',   name:'GitHub',          icon: GitBranch, color:'#58a6ff', desc:'Project board watcher, cost reporting, session attribution' },
  { id:'copilot',  name:'GitHub Copilot',  icon: Bot,       color:'#3fb950', desc:'Interactive and automated Copilot CLI sessions with cost tracking' },
  { id:'azure',    name:'Azure Storage',   icon: Cloud,     color:'#79c0ff', desc:'Azure Blob Storage access for firmware bundles' },
  { id:'obsidian', name:'Obsidian Kanban', icon: FileText,  color:'#bc8cff', desc:'Watches Obsidian Kanban boards and runs Copilot sessions per card' },
]

interface WizardProps { onComplete: () => void }

// Steps: 0=Welcome, 1=Select, 2=Configure (one per selected plugin), last=Done
export default function Wizard({ onComplete }: WizardProps) {
  const [step, setStep] = useState(0)
  const [selected, setSelected] = useState<string[]>([])
  const [configStep, setConfigStep] = useState(0) // index into selected[]
  const [configs, setConfigs] = useState<Record<string, Record<string, any>>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const stepLabels = ['Welcome', 'Plugins', ...(selected.length > 0 ? ['Configure'] : []), 'Done']
  // visual step index: 0=welcome, 1=select, 2=configure (if any), 3=done
  const visualStep = step === 0 ? 0 : step === 1 ? 1 : step === 2 && selected.length > 0 ? 2 : stepLabels.length - 1

  const togglePlugin = (id: string) =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  const goToConfigure = () => { setConfigStep(0); setStep(2) }

  const saveAndFinish = async () => {
    setSaving(true); setError(null)
    try {
      for (const [i, typeId] of selected.entries()) {
        const cfg = configs[typeId] || {}
        const { token, client_secret, ...rest } = cfg as any
        const pluginId = `${typeId}-${i === 0 ? 'main' : i}`
        await fetch('/api/plugins', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: pluginId, plugin_type: typeId, config: rest }),
        })
        // Store secret separately
        if (token) {
          await fetch(`/api/plugins/${pluginId}/credentials`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'pat', token }),
          })
        }
        if (client_secret) {
          await fetch(`/api/plugins/${pluginId}/credentials`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'client_secret', client_secret }),
          })
        }
      }
      await fetch('/api/wizard/complete', { method: 'POST' })
      onComplete()
    } catch (e: any) {
      setError(e.message || 'Failed to save configuration')
    } finally {
      setSaving(false)
    }
  }

  const currentConfigType = selected[configStep]
  const currentTypeMeta = PLUGIN_TYPES.find(p => p.id === currentConfigType)

  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', minHeight:'80vh', justifyContent:'center', padding:'16px 0' }}>
      {/* Step indicator */}
      <div style={{ display:'flex', gap:8, alignItems:'center', marginBottom:32 }}>
        {stepLabels.map((s, i) => (
          <div key={s} style={{ display:'flex', alignItems:'center', gap:8 }}>
            <div style={{
              width:28, height:28, borderRadius:'50%', display:'flex', alignItems:'center', justifyContent:'center',
              fontWeight:700, fontSize:12,
              background: i < visualStep ? 'var(--green)' : i === visualStep ? 'var(--accent)' : 'var(--surface2)',
              color: i <= visualStep ? '#fff' : 'var(--muted)',
              border: `2px solid ${i < visualStep ? 'var(--green)' : i === visualStep ? 'var(--accent)' : 'var(--border)'}`,
              transition: 'all 0.2s',
            }}>
              {i < visualStep ? <CheckCircle2 size={14}/> : i + 1}
            </div>
            <span style={{ fontSize:12, color: i === visualStep ? 'var(--text)' : 'var(--muted)', fontWeight: i === visualStep ? 600 : 400 }}>{s}</span>
            {i < stepLabels.length - 1 && <div style={{ width:32, height:1, background:'var(--border)' }}/>}
          </div>
        ))}
      </div>

      <div className="modal-card" style={{ maxWidth:580, width:'100%' }}>

        {/* STEP 0: Welcome */}
        {step === 0 && (
          <div style={{ textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16 }}>🧪</div>
            <h1 style={{ fontSize:26, fontWeight:700, marginBottom:8 }} className="brand-glow">Welcome to Gru's Lab</h1>
            <p style={{ color:'var(--muted)', marginBottom:24, lineHeight:1.6 }}>
              A web interface for running automated Copilot sessions against your GitHub project boards.
              Connect your tools to get started — you can always reconfigure later via the <strong>⚙️</strong> icon.
            </p>
            <button className="btn btn-primary" style={{ fontSize:15, padding:'10px 28px' }} onClick={() => setStep(1)}>
              Get Started <ChevronRight size={16}/>
            </button>
          </div>
        )}

        {/* STEP 1: Select plugins */}
        {step === 1 && (
          <div>
            <h2 style={{ fontSize:18, fontWeight:700, marginBottom:4 }}>Connect Plugins</h2>
            <p style={{ color:'var(--muted)', marginBottom:20, fontSize:13 }}>Select the tools you want to connect. You can add more later.</p>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:24 }}>
              {PLUGIN_TYPES.map(({ id, name, icon: Icon, color, desc }) => {
                const active = selected.includes(id)
                return (
                  <div key={id}
                    className={'card card-interactive' + (active ? ' card-active' : '')}
                    onClick={() => togglePlugin(id)}
                    style={{ position:'relative', cursor:'pointer' }}
                  >
                    {active && <div style={{ position:'absolute', top:10, right:10 }}><CheckCircle2 size={16} color="var(--green)"/></div>}
                    <div style={{ width:40, height:40, borderRadius:8, marginBottom:10, background:`color-mix(in srgb, ${color} 15%, transparent)`, display:'flex', alignItems:'center', justifyContent:'center' }}>
                      <Icon size={20} color={color}/>
                    </div>
                    <div style={{ fontWeight:600, marginBottom:4, fontSize:13 }}>{name}</div>
                    <div style={{ fontSize:11, color:'var(--muted)', lineHeight:1.4 }}>{desc}</div>
                  </div>
                )
              })}
            </div>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <button className="btn btn-ghost" onClick={() => setStep(0)}><ChevronLeft size={14}/>Back</button>
              <button className="btn btn-primary" onClick={() => selected.length > 0 ? goToConfigure() : setStep(3)}>
                {selected.length > 0 ? 'Configure' : 'Skip'} <ChevronRight size={16}/>
              </button>
            </div>
          </div>
        )}

        {/* STEP 2: Configure each plugin */}
        {step === 2 && currentTypeMeta && (
          <div>
            <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:20 }}>
              <div style={{ width:40, height:40, borderRadius:8, background:`color-mix(in srgb, ${currentTypeMeta.color} 15%, transparent)`, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>
                <currentTypeMeta.icon size={20} color={currentTypeMeta.color}/>
              </div>
              <div>
                <h2 style={{ fontSize:17, fontWeight:700, marginBottom:2 }}>Configure {currentTypeMeta.name}</h2>
                <div style={{ fontSize:11, color:'var(--muted)' }}>
                  Plugin {configStep + 1} of {selected.length}
                  {selected.length > 1 && (
                    <span style={{ marginLeft:8 }}>
                      {selected.map((id, i) => (
                        <span key={id} style={{
                          display:'inline-block', width:6, height:6, borderRadius:'50%', marginRight:3,
                          background: i === configStep ? 'var(--accent)' : i < configStep ? 'var(--green)' : 'var(--border)',
                        }}/>
                      ))}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <PluginConfigForm
              pluginType={currentTypeMeta.id}
              initialValues={configs[currentTypeMeta.id] || {}}
              onChange={vals => setConfigs(c => ({ ...c, [currentTypeMeta.id]: vals }))}
            />

            {error && <div style={{ color:'var(--red)', fontSize:12, marginTop:12 }}>⚠ {error}</div>}

            <div style={{ display:'flex', justifyContent:'space-between', marginTop:24 }}>
              <button className="btn btn-ghost" onClick={() => configStep > 0 ? setConfigStep(i => i - 1) : setStep(1)}>
                <ChevronLeft size={14}/>Back
              </button>
              {configStep < selected.length - 1 ? (
                <button className="btn btn-primary" onClick={() => setConfigStep(i => i + 1)}>
                  Next Plugin <ChevronRight size={16}/>
                </button>
              ) : (
                <button className="btn btn-primary" onClick={saveAndFinish} disabled={saving}>
                  {saving ? <><Loader2 size={14} className="spin"/>Saving…</> : <>Save & Launch 🧪</>}
                </button>
              )}
            </div>
          </div>
        )}

        {/* DONE (step 3 or after skip) */}
        {step === 3 && (
          <div style={{ textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16 }}>✓</div>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>Ready to Launch</h2>
            <p style={{ color:'var(--muted)', marginBottom:24, lineHeight:1.6 }}>
              Head to <strong>Plugins</strong> to add API tokens and fine-tune each integration.
              Use the <strong>⚙️ gear icon</strong> in the header to return here at any time.
            </p>
            <button className="btn btn-primary" style={{ fontSize:15, padding:'10px 28px' }} onClick={saveAndFinish} disabled={saving}>
              {saving ? <><Loader2 size={14} className="spin"/>Saving…</> : <>🧪 Launch Gru's Lab</>}
            </button>
          </div>
        )}

      </div>
    </div>
  )
}
