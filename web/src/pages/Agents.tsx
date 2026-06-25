import { useEffect, useState, useCallback } from 'react'
import { Bot, Plus, FileUp, GitBranch, Trash2, Edit2, X, ChevronDown, ChevronUp } from 'lucide-react'

interface Agent {
  id: string
  name: string
  description: string
  source: string
  agent_md: string
  model: string
  tools: string[]
  mcp_servers: Record<string, unknown>
  file_path: string
  repo_url: string
  repo_path: string
  repo_ref: string
  created_at: string
  updated_at: string
}

const EMPTY_AGENT: Partial<Agent> = {
  id: '', name: '', description: '', source: 'inline',
  agent_md: '', model: '', tools: [], mcp_servers: {},
}

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Partial<Agent> | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [importMode, setImportMode] = useState<'file' | 'repo' | null>(null)
  const [importPath, setImportPath] = useState('')
  const [importRepo, setImportRepo] = useState({ url: '', path: '.github/agents', ref: 'main' })
  const [error, setError] = useState('')

  const fetchAgents = useCallback(async () => {
    try {
      const r = await fetch('/api/agents')
      if (r.ok) setAgents(await r.json())
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchAgents() }, [fetchAgents])

  const save = async () => {
    if (!editing) return
    setError('')
    const method = isNew ? 'POST' : 'PUT'
    const url = isNew ? '/api/agents' : `/api/agents/${editing.id}`
    const r = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editing),
    })
    if (!r.ok) {
      const e = await r.json()
      setError(e.detail || 'Save failed')
      return
    }
    setEditing(null)
    setIsNew(false)
    fetchAgents()
  }

  const remove = async (id: string) => {
    if (!confirm(`Delete agent "${id}"?`)) return
    await fetch(`/api/agents/${id}`, { method: 'DELETE' })
    fetchAgents()
  }

  const importFromFile = async () => {
    setError('')
    const r = await fetch('/api/agents/import/file', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: importPath }),
    })
    if (!r.ok) {
      const e = await r.json()
      setError(e.detail || 'Import failed')
      return
    }
    setImportMode(null)
    setImportPath('')
    fetchAgents()
  }

  const importFromRepo = async () => {
    setError('')
    const r = await fetch('/api/agents/import/repo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_url: importRepo.url, repo_path: importRepo.path, repo_ref: importRepo.ref }),
    })
    if (!r.ok) {
      const e = await r.json()
      setError(e.detail || 'Import failed')
      return
    }
    const data = await r.json()
    setImportMode(null)
    setImportRepo({ url: '', path: '.github/agents', ref: 'main' })
    setError(`Imported ${data.count} agent(s)`)
    fetchAgents()
  }

  const sourceIcon = (s: string) => {
    switch (s) {
      case 'file': return <FileUp size={12} />
      case 'repo': return <GitBranch size={12} />
      default: return <Edit2 size={12} />
    }
  }

  if (loading) return <div className="page-container"><p style={{ color: 'var(--muted)' }}>Loading…</p></div>

  return (
    <div className="page-container">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)' }}>
          <Bot size={22} style={{ verticalAlign: 'text-bottom', marginRight: 8 }} />
          Agent Library
        </h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn-secondary" onClick={() => setImportMode(importMode === 'file' ? null : 'file')}>
            <FileUp size={14} /> Import File
          </button>
          <button className="btn-secondary" onClick={() => setImportMode(importMode === 'repo' ? null : 'repo')}>
            <GitBranch size={14} /> Import Repo
          </button>
          <button className="btn-primary" onClick={() => { setEditing({ ...EMPTY_AGENT }); setIsNew(true) }}>
            <Plus size={14} /> New Agent
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          padding: '8px 12px', borderRadius: 6, marginBottom: 12,
          background: error.startsWith('Imported') ? 'color-mix(in srgb, var(--green) 12%, transparent)' : 'color-mix(in srgb, var(--red) 12%, transparent)',
          border: `1px solid ${error.startsWith('Imported') ? 'var(--green)' : 'var(--red)'}`,
          color: error.startsWith('Imported') ? 'var(--green)' : 'var(--red)',
          fontSize: 13,
        }}>
          {error}
          <button onClick={() => setError('')} style={{ float: 'right', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* Import panels */}
      {importMode === 'file' && (
        <div className="card" style={{ marginBottom: 16, padding: 16 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Import from local file</h3>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              type="text" placeholder="Path to .agent.md file (e.g. ~/.copilot/agents/hw-check.agent.md)"
              value={importPath} onChange={e => setImportPath(e.target.value)}
              style={{ flex: 1 }}
            />
            <button className="btn-primary" onClick={importFromFile} disabled={!importPath}>Import</button>
            <button className="btn-secondary" onClick={() => setImportMode(null)}>Cancel</button>
          </div>
        </div>
      )}

      {importMode === 'repo' && (
        <div className="card" style={{ marginBottom: 16, padding: 16 }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>Import from Git repository</h3>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input
              type="text" placeholder="Repository URL"
              value={importRepo.url} onChange={e => setImportRepo(r => ({ ...r, url: e.target.value }))}
              style={{ flex: 2, minWidth: 200 }}
            />
            <input
              type="text" placeholder="Path in repo (default: .github/agents)"
              value={importRepo.path} onChange={e => setImportRepo(r => ({ ...r, path: e.target.value }))}
              style={{ flex: 1, minWidth: 150 }}
            />
            <input
              type="text" placeholder="Branch (default: main)"
              value={importRepo.ref} onChange={e => setImportRepo(r => ({ ...r, ref: e.target.value }))}
              style={{ width: 100 }}
            />
            <button className="btn-primary" onClick={importFromRepo} disabled={!importRepo.url}>Import</button>
            <button className="btn-secondary" onClick={() => setImportMode(null)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Agent editor modal */}
      {editing && (
        <div className="card" style={{ marginBottom: 16, padding: 16, borderColor: 'var(--accent)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ fontSize: 14, fontWeight: 600 }}>{isNew ? 'New Agent' : `Edit: ${editing.name}`}</h3>
            <button onClick={() => { setEditing(null); setIsNew(false) }} style={{ background: 'none', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}>
              <X size={16} />
            </button>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>ID</label>
              <input
                type="text" value={editing.id || ''} disabled={!isNew}
                onChange={e => setEditing(p => ({ ...p, id: e.target.value }))}
                placeholder="e.g. hw-check"
                style={{ width: '100%', opacity: isNew ? 1 : 0.6 }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Name</label>
              <input
                type="text" value={editing.name || ''}
                onChange={e => setEditing(p => ({ ...p, name: e.target.value }))}
                placeholder="HW Check Agent"
                style={{ width: '100%' }}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Description</label>
              <input
                type="text" value={editing.description || ''}
                onChange={e => setEditing(p => ({ ...p, description: e.target.value }))}
                placeholder="Short description of what this agent does"
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Model</label>
              <input
                type="text" value={editing.model || ''}
                onChange={e => setEditing(p => ({ ...p, model: e.target.value }))}
                placeholder="claude-sonnet-4.6"
                style={{ width: '100%' }}
              />
            </div>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>Tools (comma-separated)</label>
              <input
                type="text" value={(editing.tools || []).join(', ')}
                onChange={e => setEditing(p => ({ ...p, tools: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))}
                placeholder="execute, read, search, github/*"
                style={{ width: '100%' }}
              />
            </div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', display: 'block', marginBottom: 4 }}>
              Agent Markdown (full .agent.md content — frontmatter + prompt body)
            </label>
            <textarea
              value={editing.agent_md || ''}
              onChange={e => setEditing(p => ({ ...p, agent_md: e.target.value }))}
              placeholder={"---\nname: hw-check\ndescription: ...\nmodel: claude-sonnet-4.6\ntools:\n  - execute\n  - read\n---\n\nYou are the HW Check agent..."}
              rows={14}
              style={{ width: '100%', fontFamily: 'ui-monospace, "SFMono-Regular", Menlo, monospace', fontSize: 12 }}
            />
          </div>

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-secondary" onClick={() => { setEditing(null); setIsNew(false) }}>Cancel</button>
            <button className="btn-primary" onClick={save} disabled={!editing.id || !editing.name}>
              {isNew ? 'Create' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {/* Agent list */}
      {agents.length === 0 && !editing ? (
        <div className="card" style={{ padding: 32, textAlign: 'center' }}>
          <Bot size={40} style={{ color: 'var(--muted)', marginBottom: 12 }} />
          <p style={{ color: 'var(--muted)', marginBottom: 8 }}>No agents configured yet</p>
          <p style={{ color: 'var(--muted)', fontSize: 12 }}>
            Create an inline agent, import from a file, or sync from a Git repository.
          </p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {agents.map(a => (
            <div key={a.id} className="card" style={{ padding: 0, overflow: 'hidden' }}>
              {/* Header row */}
              <div
                style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', cursor: 'pointer' }}
                onClick={() => setExpanded(expanded === a.id ? null : a.id)}
              >
                <Bot size={18} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>{a.name}</span>
                  <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--muted)' }}>{a.id}</span>
                  {a.description && (
                    <p style={{ fontSize: 12, color: 'var(--muted)', margin: '2px 0 0', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {a.description}
                    </p>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
                  <span className="badge" title={`Source: ${a.source}`}>
                    {sourceIcon(a.source)} {a.source}
                  </span>
                  {a.model && <span className="badge" style={{ background: 'color-mix(in srgb, var(--purple) 15%, transparent)', color: 'var(--purple)' }}>{a.model}</span>}
                  {expanded === a.id ? <ChevronUp size={14} style={{ color: 'var(--muted)' }} /> : <ChevronDown size={14} style={{ color: 'var(--muted)' }} />}
                </div>
              </div>

              {/* Expanded details */}
              {expanded === a.id && (
                <div style={{ borderTop: '1px solid var(--border)', padding: '12px 16px', background: 'var(--surface2)' }}>
                  <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap' }}>
                    {a.tools.length > 0 && (
                      <div>
                        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Tools</span>
                        <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
                          {a.tools.map(t => (
                            <span key={t} className="badge">{t}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {a.file_path && (
                      <div>
                        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>File</span>
                        <p style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'monospace', marginTop: 4 }}>{a.file_path}</p>
                      </div>
                    )}
                    {a.repo_url && (
                      <div>
                        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Repo</span>
                        <p style={{ fontSize: 12, color: 'var(--text)', fontFamily: 'monospace', marginTop: 4 }}>{a.repo_url} @ {a.repo_ref}</p>
                      </div>
                    )}
                  </div>

                  {a.agent_md && (
                    <details style={{ marginBottom: 12 }}>
                      <summary style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', cursor: 'pointer', marginBottom: 4 }}>Agent Markdown</summary>
                      <pre style={{
                        fontSize: 11, padding: 12, borderRadius: 6,
                        background: 'var(--surface)', border: '1px solid var(--border)',
                        color: 'var(--text)', overflow: 'auto', maxHeight: 300,
                        whiteSpace: 'pre-wrap',
                      }}>{a.agent_md}</pre>
                    </details>
                  )}

                  <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn-secondary" onClick={(e) => { e.stopPropagation(); setEditing(a); setIsNew(false) }}>
                      <Edit2 size={14} /> Edit
                    </button>
                    <button className="btn-danger" onClick={(e) => { e.stopPropagation(); remove(a.id) }}>
                      <Trash2 size={14} /> Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
