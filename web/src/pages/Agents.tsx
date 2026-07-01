import { useEffect, useState, useCallback, useRef } from 'react'
import { Bot, Plus, FileUp, GitBranch, Trash2, Save, X, Upload, AlertCircle, ChevronRight, Tag, Cpu } from 'lucide-react'

interface Agent {
  id: string
  name: string
  description: string
  source: string
  agent_md: string
  model: string
  tools: string[]
  skills: string[]
  mcp_servers: Record<string, unknown>
  file_path: string
  repo_url: string
  repo_path: string
  repo_ref: string
  created_at: string
  updated_at: string
}

interface Pipeline {
  id: string
  name: string
  stages: { column_name: string; agent_id: string }[]
}

const EMPTY_AGENT: Partial<Agent> = {
  id: '', name: '', description: '', source: 'inline',
  agent_md: '', model: '', tools: [], skills: [], mcp_servers: {},
}

export default function Agents() {
  const [agents, setAgents]       = useState<Agent[]>([])
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [selected, setSelected]   = useState<Agent | null>(null)
  const [editing, setEditing]     = useState<Partial<Agent> | null>(null)
  const [isNew, setIsNew]         = useState(false)
  const [dirty, setDirty]         = useState(false)
  const [saving, setSaving]       = useState(false)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState('')
  const [importMode, setImportMode] = useState<'file' | 'repo' | 'upload' | null>(null)
  const [importPath, setImportPath] = useState('')
  const [importRepo, setImportRepo] = useState({ url: '', path: '.github/agents', ref: 'main' })
  const uploadRef = useRef<HTMLInputElement>(null)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [ar, pr] = await Promise.all([fetch('/api/agents'), fetch('/api/pipelines')])
      if (ar.ok) setAgents(await ar.json())
      if (pr.ok) setPipelines(await pr.json())
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  function usedBy(agentId: string) {
    const refs: { pipeline: string; stage: string }[] = []
    for (const p of pipelines) {
      for (const s of p.stages || []) {
        if (s.agent_id === agentId) refs.push({ pipeline: p.name || p.id, stage: s.column_name })
      }
    }
    return refs
  }

  function startNew() {
    setEditing({ ...EMPTY_AGENT })
    setIsNew(true)
    setSelected(null)
    setDirty(false)
  }

  function selectAgent(a: Agent) {
    if (dirty && !confirm('Discard unsaved changes?')) return
    setSelected(a)
    setEditing({ ...a })
    setIsNew(false)
    setDirty(false)
  }

  function field<K extends keyof Agent>(k: K, v: Agent[K]) {
    setEditing(e => ({ ...e, [k]: v }))
    setDirty(true)
  }

  async function save() {
    if (!editing) return
    setSaving(true)
    setError('')
    try {
      const method = isNew ? 'POST' : 'PUT'
      const url = isNew ? '/api/agents' : `/api/agents/${editing.id}`
      const r = await fetch(url, {
        method, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editing),
      })
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail || 'Save failed') }
      const saved = await r.json()
      await fetchAll()
      setSelected(saved)
      setEditing({ ...saved })
      setIsNew(false)
      setDirty(false)
    } catch (e: unknown) { setError(String(e)) }
    finally { setSaving(false) }
  }

  async function remove() {
    if (!selected || !confirm(`Delete agent "${selected.id}"?`)) return
    await fetch(`/api/agents/${selected.id}`, { method: 'DELETE' })
    setSelected(null)
    setEditing(null)
    setDirty(false)
    fetchAll()
  }

  async function importFromFile() {
    const r = await fetch('/api/agents/import/file', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: importPath }),
    })
    if (!r.ok) { const e = await r.json(); setError(e.detail || 'Import failed'); return }
    setImportMode(null); setImportPath(''); fetchAll()
  }

  async function importFromRepo() {
    const r = await fetch('/api/agents/import/repo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: importRepo.url, repo_path: importRepo.path, repo_ref: importRepo.ref }),
    })
    if (!r.ok) { const e = await r.json(); setError(e.detail || 'Import failed'); return }
    const data = await r.json()
    setImportMode(null)
    setImportRepo({ url: '', path: '.github/agents', ref: 'main' })
    setError(`Imported ${data.count} agent(s)`)
    fetchAll()
  }

  async function importFromUpload(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/agents/import/upload', { method: 'POST', body: fd })
    if (!r.ok) { const e = await r.json(); setError(e.detail || 'Upload failed'); return }
    const saved = await r.json()
    await fetchAll()
    setSelected(saved)
    setEditing({ ...saved })
    setIsNew(false)
    setDirty(false)
    setImportMode(null)
  }

  if (loading) return <div style={{ padding: 32, color: 'var(--muted)' }}>Loading…</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {/* Header */}
      <div style={{ padding: '20px 24px 16px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <Bot size={18} style={{ color: 'var(--accent)' }} />
        <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0, flex: 1 }}>Agent Library</h1>
        <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }} onClick={() => setImportMode(m => m === 'file' ? null : 'file')}>
          <FileUp size={13} /> From file
        </button>
        <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }} onClick={() => uploadRef.current?.click()}>
          <Upload size={13} /> Upload .agent.md
        </button>
        <input ref={uploadRef} type="file" accept=".md" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) importFromUpload(f); e.target.value = '' }} />
        <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }} onClick={() => setImportMode(m => m === 'repo' ? null : 'repo')}>
          <GitBranch size={13} /> From repo
        </button>
        <button className="btn btn-primary" style={{ fontSize: 12, gap: 5 }} onClick={startNew}>
          <Plus size={13} /> New agent
        </button>
      </div>

      {/* Import panels */}
      {importMode === 'file' && (
        <div style={{ padding: '10px 24px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, background: 'var(--surface)' }}>
          <input className="input" style={{ flex: 1, fontSize: 12 }} placeholder="Path to .agent.md on server (e.g. /workspace/.github/agents/hw-check.agent.md)"
            value={importPath} onChange={e => setImportPath(e.target.value)} onKeyDown={e => e.key === 'Enter' && importFromFile()} autoFocus />
          <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={importFromFile} disabled={!importPath}>Import</button>
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => setImportMode(null)}>Cancel</button>
        </div>
      )}
      {importMode === 'repo' && (
        <div style={{ padding: '10px 24px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8, flexWrap: 'wrap', background: 'var(--surface)' }}>
          <input className="input" style={{ flex: 2, minWidth: 200, fontSize: 12 }} placeholder="Repository URL"
            value={importRepo.url} onChange={e => setImportRepo(r => ({ ...r, url: e.target.value }))} />
          <input className="input" style={{ flex: 1, minWidth: 140, fontSize: 12 }} placeholder="Path (default: .github/agents)"
            value={importRepo.path} onChange={e => setImportRepo(r => ({ ...r, path: e.target.value }))} />
          <input className="input" style={{ width: 90, fontSize: 12 }} placeholder="Branch"
            value={importRepo.ref} onChange={e => setImportRepo(r => ({ ...r, ref: e.target.value }))} />
          <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={importFromRepo} disabled={!importRepo.url}>Import</button>
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => setImportMode(null)}>Cancel</button>
        </div>
      )}

      {error && (
        <div style={{ margin: '0 24px 0', padding: '8px 12px', borderRadius: 6, background: error.startsWith('Imported') ? 'color-mix(in srgb, var(--green) 12%, transparent)' : 'color-mix(in srgb, var(--red) 12%, transparent)', color: error.startsWith('Imported') ? 'var(--green)' : 'var(--red)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={13} />{error}
          <button onClick={() => setError('')} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'inherit' }}><X size={12} /></button>
        </div>
      )}

      {/* Body: list + editor */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>

        {/* Agent list */}
        <div style={{ width: 230, borderRight: '1px solid var(--border)', overflowY: 'auto', flexShrink: 0 }}>
          {agents.length === 0 && !isNew ? (
            <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>
              No agents yet.<br />Create one or import.
            </div>
          ) : agents.map(a => {
            const refs = usedBy(a.id)
            return (
              <div key={a.id} onClick={() => selectAgent(a)} style={{
                padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)',
                background: selected?.id === a.id ? 'color-mix(in srgb, var(--accent) 10%, var(--surface))' : 'transparent',
                borderLeft: selected?.id === a.id ? '3px solid var(--accent)' : '3px solid transparent',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Bot size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                  <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name || a.id}</span>
                  {selected?.id === a.id && <ChevronRight size={11} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
                </div>
                {a.description && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.description}</div>}
                <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                  {a.model && <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, fontWeight: 600, background: 'color-mix(in srgb, var(--purple) 15%, transparent)', color: 'var(--purple)' }}>{a.model.split('-').slice(0,2).join('-')}</span>}
                  {refs.map(r => <span key={r.stage} style={{ fontSize: 9, padding: '1px 5px', borderRadius: 3, fontWeight: 600, background: 'color-mix(in srgb, var(--green) 12%, transparent)', color: 'var(--green)' }}>{r.stage}</span>)}
                </div>
              </div>
            )
          })}
        </div>

        {/* Editor panel */}
        {(editing || isNew) ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

            {/* Editor toolbar */}
            <div style={{ padding: '10px 18px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>{isNew ? 'New Agent' : editing?.name || editing?.id}</span>
              {dirty && <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 3, background: 'color-mix(in srgb, var(--yellow) 12%, transparent)', color: 'var(--yellow)' }}>unsaved</span>}
              {!isNew && (
                <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5, color: 'var(--red)' }} onClick={remove}>
                  <Trash2 size={13} /> Delete
                </button>
              )}
              <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => { if (!dirty || confirm('Discard?')) { setEditing(null); setIsNew(false); setDirty(false) } }}>Cancel</button>
              <button className="btn btn-primary" style={{ fontSize: 12, gap: 5 }} onClick={save} disabled={saving || !editing?.id || !editing?.name}>
                <Save size={13} />{saving ? 'Saving…' : 'Save'}
              </button>
            </div>

            {/* Two-column form */}
            <div style={{ flex: 1, overflow: 'auto', padding: '16px 18px', display: 'flex', gap: 20 }}>

              {/* Left: metadata */}
              <div style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 14 }}>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>ID</label>
                  <input className="input" style={{ width: '100%', fontSize: 13, opacity: isNew ? 1 : 0.6 }}
                    value={editing?.id || ''} disabled={!isNew}
                    onChange={e => field('id', e.target.value)} placeholder="hil-hw-check" />
                </div>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Display Name</label>
                  <input className="input" style={{ width: '100%', fontSize: 13 }}
                    value={editing?.name || ''} onChange={e => field('name', e.target.value)} placeholder="HW Check" />
                </div>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Description</label>
                  <textarea className="input" rows={3} style={{ width: '100%', fontSize: 12, resize: 'vertical' }}
                    value={editing?.description || ''} onChange={e => field('description', e.target.value)}
                    placeholder="Short description of what this agent does" />
                </div>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}><Cpu size={11} style={{ verticalAlign: 'middle' }} /> Model</label>
                  <input className="input" style={{ width: '100%', fontSize: 13 }}
                    value={editing?.model || ''} onChange={e => field('model', e.target.value)} placeholder="claude-sonnet-4.6" />
                </div>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}><Tag size={11} style={{ verticalAlign: 'middle' }} /> Tools (comma-separated)</label>
                  <input className="input" style={{ width: '100%', fontSize: 12 }}
                    value={(editing?.tools || []).join(', ')}
                    onChange={e => field('tools', e.target.value.split(',').map(s => s.trim()).filter(Boolean) as unknown as string[])}
                    placeholder="execute, read, github/*" />
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                    {(editing?.tools || []).map(t => (
                      <span key={t} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: 'color-mix(in srgb, var(--cyan) 14%, transparent)', color: 'var(--cyan)', fontWeight: 600 }}>{t}</span>
                    ))}
                  </div>
                </div>

                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
                    🔧 Skills (one per line, relative to working_dir)
                  </label>
                  <textarea className="input" rows={4} style={{ width: '100%', fontSize: 12, fontFamily: 'ui-monospace, monospace', resize: 'vertical' }}
                    value={(editing?.skills || []).join('\n')}
                    onChange={e => field('skills', e.target.value.split('\n').map(s => s.trim()).filter(Boolean) as unknown as string[])}
                    placeholder={'skills/hil-stress/hil-preflight.sh\nskills/hil-stress/hil-needs-human.sh'} />
                  <p style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                    Declared skill dependencies. The orchestrator will warn if any are missing before starting a session.
                  </p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                    {(editing?.skills || []).map((sk: string) => {
                      const label = sk.replace(/^skills\/[\w-]+\//, '').replace('.sh', '')
                      return (
                        <span key={sk} title={sk} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: 'color-mix(in srgb, var(--green) 12%, transparent)', color: 'var(--green)', fontWeight: 600 }}>{label}</span>
                      )
                    })}
                  </div>
                </div>

                {/* Used by */}
                {!isNew && selected && (() => {
                  const refs = usedBy(selected.id)
                  if (!refs.length) return null
                  return (
                    <div>
                      <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 6 }}>Used by stages</label>
                      {refs.map(r => (
                        <div key={r.stage} style={{ fontSize: 12, padding: '4px 8px', borderRadius: 4, background: 'color-mix(in srgb, var(--green) 10%, transparent)', color: 'var(--green)', marginBottom: 4 }}>
                          {r.pipeline} → {r.stage}
                        </div>
                      ))}
                    </div>
                  )
                })()}
              </div>

              {/* Right: agent_md editor */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--muted)', flex: 1 }}>Agent prompt (full .agent.md)</label>
                  <label className="btn btn-ghost" style={{ fontSize: 11, padding: '3px 8px', cursor: 'pointer', gap: 4 }}>
                    <Upload size={10} /> Load file
                    <input type="file" accept=".md,.txt" hidden onChange={e => {
                      const f = e.target.files?.[0]
                      if (f) f.text().then(t => { field('agent_md', t); setDirty(true) })
                    }} />
                  </label>
                </div>
                <textarea
                  value={editing?.agent_md || ''}
                  onChange={e => field('agent_md', e.target.value)}
                  spellCheck={false}
                  placeholder={"---\nname: hw-check\ndescription: HIL HW-Check stage\nmodel: claude-sonnet-4.6\ntools:\n  - execute\n  - read\n---\n\n# Your instructions here…"}
                  style={{
                    flex: 1, resize: 'none', border: '1px solid var(--border)', borderRadius: 6,
                    padding: '12px 14px', fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, monospace',
                    fontSize: 12.5, lineHeight: 1.6, background: 'var(--bg)', color: 'var(--text)',
                    outline: 'none',
                  }}
                />
              </div>
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', flexDirection: 'column', gap: 10 }}>
            <Bot size={36} style={{ opacity: 0.2 }} />
            <span style={{ fontSize: 14 }}>Select an agent to edit</span>
            <button className="btn btn-primary" style={{ fontSize: 12, gap: 5, marginTop: 4 }} onClick={startNew}>
              <Plus size={13} /> New agent
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
