import { useState } from 'react'
import { CheckCircle2, GitBranch, Bot, Cloud, FileText, ChevronRight, ChevronLeft } from 'lucide-react'

const PLUGIN_TYPES = [
  { id:'github',   name:'GitHub',          icon: GitBranch,   color:'#58a6ff', desc:'Project board watcher, cost reporting, session attribution' },
  { id:'copilot',  name:'GitHub Copilot',  icon: Bot,      color:'#3fb950', desc:'Interactive and automated Copilot CLI sessions with cost tracking' },
  { id:'azure',    name:'Azure Storage',   icon: Cloud,    color:'#79c0ff', desc:'Azure Blob Storage access for firmware bundles' },
  { id:'obsidian', name:'Obsidian Kanban', icon: FileText, color:'#bc8cff', desc:'Watches Obsidian Kanban boards and runs Copilot sessions per card' },
]

interface WizardProps { onComplete: () => void }

export default function Wizard({ onComplete }: WizardProps) {
  const [step, setStep] = useState(0)
  const [selected, setSelected] = useState<string[]>([])

  const steps = ['Welcome', 'Plugins', 'Done']

  const togglePlugin = (id: string) =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  const complete = async () => {
    await fetch('/api/wizard/complete', { method:'POST' })
    onComplete()
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', minHeight:'80vh', justifyContent:'center' }}>
      {/* Step indicator */}
      <div style={{ display:'flex', gap:8, alignItems:'center', marginBottom:32 }}>
        {steps.map((s, i) => (
          <div key={s} style={{ display:'flex', alignItems:'center', gap:8 }}>
            <div style={{
              width:28, height:28, borderRadius:'50%', display:'flex', alignItems:'center', justifyContent:'center',
              fontWeight:700, fontSize:12,
              background: i < step ? 'var(--green)' : i === step ? 'var(--accent)' : 'var(--surface2)',
              color: i <= step ? '#fff' : 'var(--muted)',
              border: `2px solid ${i < step ? 'var(--green)' : i === step ? 'var(--accent)' : 'var(--border)'}`,
            }}>
              {i < step ? <CheckCircle2 size={14}/> : i + 1}
            </div>
            <span style={{ fontSize:12, color: i === step ? 'var(--text)' : 'var(--muted)', fontWeight: i === step ? 600 : 400 }}>{s}</span>
            {i < steps.length - 1 && <div style={{ width:32, height:1, background:'var(--border)' }}/>}
          </div>
        ))}
      </div>

      {/* Wizard card */}
      <div className="modal-card" style={{ maxWidth:640, width:'100%' }}>
        {step === 0 && (
          <div style={{ textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16 }}>🧪</div>
            <h1 style={{ fontSize:26, fontWeight:700, marginBottom:8 }} className="brand-glow">Welcome to Gru's Lab</h1>
            <p style={{ color:'var(--muted)', marginBottom:24, lineHeight:1.6 }}>
              Gru's Lab is a web interface for running automated Copilot sessions against your GitHub project boards.
              Connect your tools to get started — you can always reconfigure later via the <strong>⚙️ Settings</strong> icon.
            </p>
            <button className="btn btn-primary" style={{ fontSize:15, padding:'10px 28px' }} onClick={() => setStep(1)}>
              Get Started <ChevronRight size={16}/>
            </button>
          </div>
        )}

        {step === 1 && (
          <div>
            <h2 style={{ fontSize:18, fontWeight:700, marginBottom:4 }}>Connect Plugins</h2>
            <p style={{ color:'var(--muted)', marginBottom:20, fontSize:13 }}>
              Select the tools you want to connect. You can add more later.
            </p>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginBottom:24 }}>
              {PLUGIN_TYPES.map(({ id, name, icon: Icon, color, desc }) => {
                const active = selected.includes(id)
                return (
                  <div key={id}
                    className={'card card-interactive' + (active ? ' card-active' : '')}
                    onClick={() => togglePlugin(id)}
                    style={{ position:'relative' }}
                  >
                    {active && (
                      <div style={{ position:'absolute', top:10, right:10 }}>
                        <CheckCircle2 size={16} color="var(--green)"/>
                      </div>
                    )}
                    <div style={{
                      width:40, height:40, borderRadius:8, marginBottom:10,
                      background:`color-mix(in srgb, ${color} 15%, transparent)`,
                      display:'flex', alignItems:'center', justifyContent:'center'
                    }}>
                      <Icon size={20} color={color}/>
                    </div>
                    <div style={{ fontWeight:600, marginBottom:4 }}>{name}</div>
                    <div style={{ fontSize:11, color:'var(--muted)', lineHeight:1.4 }}>{desc}</div>
                  </div>
                )
              })}
            </div>
            <p style={{ fontSize:11, color:'var(--muted)', marginBottom:20 }}>
              Detailed configuration (tokens, project numbers) will be available on the Plugins page after setup.
            </p>
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <button className="btn btn-ghost" onClick={() => setStep(0)}><ChevronLeft size={14}/>Back</button>
              <button className="btn btn-primary" onClick={() => setStep(2)}>
                Continue <ChevronRight size={16}/>
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div style={{ textAlign:'center' }}>
            <div style={{ fontSize:48, marginBottom:16 }}>✓</div>
            <h2 style={{ fontSize:20, fontWeight:700, marginBottom:8 }}>Ready to Launch</h2>
            <p style={{ color:'var(--muted)', marginBottom:24, lineHeight:1.6 }}>
              Your environment is configured. Head to the <strong>Plugins</strong> page to add API tokens
              and fine-tune each integration. Use the <strong>⚙️ gear icon</strong> in the header to return to
              settings at any time.
            </p>
            {selected.length > 0 && (
              <div style={{ display:'flex', gap:8, justifyContent:'center', flexWrap:'wrap', marginBottom:24 }}>
                {selected.map(id => {
                  const pt = PLUGIN_TYPES.find(p => p.id === id)!
                  return <span key={id} className="badge badge-info">{pt.name}</span>
                })}
              </div>
            )}
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <button className="btn btn-ghost" onClick={() => setStep(1)}><ChevronLeft size={14}/>Back</button>
              <button className="btn btn-primary" style={{ fontSize:15, padding:'10px 28px' }} onClick={complete}>
                🧪 Launch Gru's Lab
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
