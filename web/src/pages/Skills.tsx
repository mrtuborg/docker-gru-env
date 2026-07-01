import { useEffect, useState, useCallback, useRef } from 'react'
import { Wrench, Plus, Trash2, RefreshCw, FileText, Terminal, Save, X, ChevronRight, Upload, AlertCircle, Download } from 'lucide-react'

interface SkillFile { name: string; size: number }
interface Skill {
  id: string
  name: string
  description: string
  files: SkillFile[]
  writable: boolean
  path: string
}
interface FileContent { skill_id: string; filename: string; content: string }

function fileIcon(name: string) {
  if (name.endsWith('.sh')) return <Terminal size={12} style={{ color: 'var(--green)', flexShrink: 0 }} />
  return <FileText size={12} style={{ color: 'var(--accent)', flexShrink: 0 }} />
}

function isEditable(name: string) {
  return name.endsWith('.sh') || name.endsWith('.md') || name.endsWith('.py') || name.endsWith('.txt')
}

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Skill | null>(null)
  const [openFile, setOpenFile] = useState<FileContent | null>(null)
  const [editContent, setEditContent] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState('')
  const [newSkillId, setNewSkillId] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [creating, setCreating] = useState(false)
  const importZipRef = useRef<HTMLInputElement>(null)
  const uploadFileRef = useRef<HTMLInputElement>(null)

  const loadSkills = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/skills')
      setSkills(await r.json())
    } catch { setError('Failed to load skills') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { loadSkills() }, [loadSkills])

  async function selectSkill(s: Skill) {
    setSelected(s)
    setOpenFile(null)
    setDirty(false)
    // Refresh skill detail
    try {
      const r = await fetch(`/api/skills/${s.id}`)
      const detail = await r.json()
      setSelected(detail)
    } catch {}
  }

  async function openSkillFile(skill: Skill, filename: string) {
    if (!isEditable(filename)) return
    try {
      const r = await fetch(`/api/skills/${skill.id}/files/${filename}`)
      const data = await r.json()
      setOpenFile(data)
      setEditContent(data.content)
      setDirty(false)
    } catch { setError('Failed to read file') }
  }

  async function saveFile() {
    if (!openFile || !selected) return
    setSaving(true)
    try {
      const r = await fetch(`/api/skills/${openFile.skill_id}/files/${openFile.filename}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent }),
      })
      if (!r.ok) throw new Error(await r.text())
      setDirty(false)
      // If skill was read-only, it's now been copied to writable — refresh
      const sr = await fetch(`/api/skills/${selected.id}`)
      setSelected(await sr.json())
      await loadSkills()
    } catch (e: unknown) { setError(String(e)) }
    finally { setSaving(false) }
  }

  async function deleteSkill(id: string) {
    if (!confirm(`Delete skill "${id}"? This only removes the writable copy.`)) return
    await fetch(`/api/skills/${id}`, { method: 'DELETE' })
    setSelected(null)
    setOpenFile(null)
    loadSkills()
  }

  async function syncWorkspace() {
    setSyncing(true)
    try {
      const r = await fetch('/api/skills/sync/workspace', { method: 'POST' })
      const d = await r.json()
      await loadSkills()
      alert(`Synced ${d.count} skill(s): ${d.synced.join(', ')}`)
    } catch { setError('Sync failed') }
    finally { setSyncing(false) }
  }

  async function createSkill() {
    if (!newSkillId.trim()) return
    setCreating(true)
    try {
      const r = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: newSkillId.trim() }),
      })
      if (!r.ok) throw new Error(await r.text())
      const s = await r.json()
      await loadSkills()
      setShowNew(false)
      setNewSkillId('')
      selectSkill(s)
    } catch (e: unknown) { setError(String(e)) }
    finally { setCreating(false) }
  }

  function exportSkill(id: string) {
    window.open(`/api/skills/${id}/export`, '_blank')
  }

  async function importZip(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await fetch('/api/skills/import/zip', { method: 'POST', body: fd })
      if (!r.ok) throw new Error(await r.text())
      const s = await r.json()
      await loadSkills()
      alert(`Imported skill "${s.id}" (${s.count} files)`)
    } catch (e: unknown) { setError(String(e)) }
  }

  async function uploadFileToSkill(file: File) {
    if (!selected) return
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await fetch(`/api/skills/${selected.id}/files/upload`, { method: 'POST', body: fd })
      if (!r.ok) throw new Error(await r.text())
      const detail = await fetch(`/api/skills/${selected.id}`)
      setSelected(await detail.json())
      await loadSkills()
    } catch (e: unknown) { setError(String(e)) }
  }

  const closeFile = () => {
    if (dirty && !confirm('Discard unsaved changes?')) return
    setOpenFile(null)
    setDirty(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, height: '100%' }}>

      {/* ── Header ── */}
      <div style={{ padding: '20px 24px 16px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <Wrench size={18} style={{ color: 'var(--accent)' }} />
        <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0, flex: 1 }}>Skills</h1>
        <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }}
          onClick={syncWorkspace} disabled={syncing}>
          <RefreshCw size={13} style={syncing ? { animation: 'spin 1s linear infinite' } : {}} />
          {syncing ? 'Syncing…' : 'Sync workspace'}
        </button>
        <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }}
          onClick={() => importZipRef.current?.click()}>
          <Upload size={13} /> Import zip
        </button>
        <input ref={importZipRef} type="file" accept=".zip" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files?.[0]; if (f) importZip(f); e.target.value = '' }} />
        <button className="btn btn-primary" style={{ fontSize: 12, gap: 5 }}
          onClick={() => { setShowNew(true) }}>
          <Plus size={13} /> New skill
        </button>
      </div>

      {error && (
        <div style={{ margin: '8px 24px', padding: '8px 12px', borderRadius: 6, background: 'color-mix(in srgb, var(--red) 12%, transparent)', color: 'var(--red)', fontSize: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          <AlertCircle size={13} />{error}
          <button onClick={() => setError('')} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--red)' }}><X size={12} /></button>
        </div>
      )}

      {/* ── New skill form ── */}
      {showNew && (
        <div style={{ margin: '0 24px 8px', padding: '12px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            className="input"
            style={{ flex: 1, fontSize: 13 }}
            placeholder="skill-id (e.g. my-skill)"
            value={newSkillId}
            onChange={e => setNewSkillId(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createSkill()}
            autoFocus
          />
          <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={createSkill} disabled={creating}>
            {creating ? 'Creating…' : 'Create'}
          </button>
          <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => { setShowNew(false); setNewSkillId('') }}>Cancel</button>
        </div>
      )}

      {/* ── Body: skill list + detail ── */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>

        {/* Skill list */}
        <div style={{ width: 220, borderRight: '1px solid var(--border)', overflowY: 'auto', flexShrink: 0 }}>
          {loading ? (
            <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13 }}>Loading…</div>
          ) : skills.length === 0 ? (
            <div style={{ padding: 24, color: 'var(--muted)', fontSize: 13 }}>No skills found.<br />Sync workspace or create one.</div>
          ) : skills.map(s => (
            <div key={s.id}
              onClick={() => selectSkill(s)}
              style={{
                padding: '10px 14px', cursor: 'pointer', borderBottom: '1px solid var(--border)',
                background: selected?.id === s.id ? 'color-mix(in srgb, var(--accent) 10%, var(--surface))' : 'transparent',
                borderLeft: selected?.id === s.id ? '3px solid var(--accent)' : '3px solid transparent',
              }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Wrench size={12} style={{ color: s.writable ? 'var(--accent)' : 'var(--muted)', flexShrink: 0 }} />
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
              </div>
              {s.description && (
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.description}
                </div>
              )}
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3 }}>
                {s.files.length} file{s.files.length !== 1 ? 's' : ''} · {s.writable ? 'writable' : 'workspace'}
              </div>
            </div>
          ))}
        </div>

        {/* Detail panel */}
        {selected ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

            {/* Skill header */}
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
              <div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{selected.name}</div>
                {selected.description && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{selected.description}</div>}
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2, fontFamily: 'monospace' }}>{selected.path}</div>
              </div>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                {!selected.writable && (
                  <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'color-mix(in srgb, var(--muted) 12%, transparent)', color: 'var(--muted)' }}>
                    read-only (workspace)
                  </span>
                )}
                <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5 }}
                  onClick={() => exportSkill(selected.id)}>
                  <Download size={13} /> Export zip
                </button>
                {selected.writable && (
                  <button className="btn btn-ghost" style={{ fontSize: 12, gap: 5, color: 'var(--red)' }}
                    onClick={() => deleteSkill(selected.id)}>
                    <Trash2 size={13} /> Delete
                  </button>
                )}
              </div>
            </div>

            {/* File list + editor */}
            <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>

              {/* File list */}
              <div style={{ width: 180, borderRight: '1px solid var(--border)', overflowY: 'auto', flexShrink: 0, display: 'flex', flexDirection: 'column' }}>
                <div style={{ flex: 1 }}>
                {(selected.files as (SkillFile | string)[]).map(f => {
                  const name = typeof f === 'string' ? f : f.name
                  const size = typeof f === 'string' ? null : f.size
                  const active = openFile?.filename === name
                  return (
                    <div key={name}
                      onClick={() => openSkillFile(selected, name)}
                      style={{
                        padding: '8px 14px', cursor: isEditable(name) ? 'pointer' : 'default',
                        display: 'flex', alignItems: 'center', gap: 7,
                        borderBottom: '1px solid var(--border)',
                        background: active ? 'color-mix(in srgb, var(--accent) 10%, var(--surface))' : 'transparent',
                        opacity: isEditable(name) ? 1 : 0.5,
                      }}>
                      {fileIcon(name)}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12, fontWeight: active ? 600 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</div>
                        {size != null && <div style={{ fontSize: 10, color: 'var(--muted)' }}>{size < 1024 ? `${size}B` : `${(size/1024).toFixed(1)}KB`}</div>}
                      </div>
                      {active && <ChevronRight size={11} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
                    </div>
                  )
                })}
                </div>
                {/* Upload file into this skill */}
                <div style={{ padding: '8px 10px', borderTop: '1px solid var(--border)', flexShrink: 0 }}>
                  <input ref={uploadFileRef} type="file" style={{ display: 'none' }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) uploadFileToSkill(f); e.target.value = '' }} />
                  <button className="btn btn-ghost" style={{ fontSize: 11, gap: 4, width: '100%' }}
                    onClick={() => uploadFileRef.current?.click()}>
                    <Upload size={11} /> Upload file
                  </button>
                </div>
              </div>

              {/* Editor */}
              {openFile ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                    {fileIcon(openFile.filename)}
                    <span style={{ fontSize: 13, fontWeight: 600, fontFamily: 'monospace', flex: 1 }}>{openFile.filename}</span>
                    {dirty && <span style={{ fontSize: 11, color: 'var(--yellow)', padding: '1px 6px', borderRadius: 3, background: 'color-mix(in srgb, var(--yellow) 12%, transparent)' }}>unsaved</span>}
                    <button className="btn btn-primary" style={{ fontSize: 12, gap: 5 }} onClick={saveFile} disabled={saving || !dirty}>
                      <Save size={13} />{saving ? 'Saving…' : 'Save'}
                    </button>
                    <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 6px' }} onClick={closeFile}>
                      <X size={14} />
                    </button>
                  </div>
                  <textarea
                    value={editContent}
                    onChange={e => { setEditContent(e.target.value); setDirty(true) }}
                    spellCheck={false}
                    style={{
                      flex: 1, resize: 'none', border: 'none', outline: 'none',
                      padding: '14px 18px', fontFamily: 'monospace', fontSize: 12.5,
                      lineHeight: 1.6, background: 'var(--bg)', color: 'var(--text)',
                    }}
                  />
                </div>
              ) : (
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', flexDirection: 'column', gap: 8 }}>
                  <Upload size={28} style={{ opacity: 0.3 }} />
                  <span style={{ fontSize: 13 }}>Select a file to edit</span>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', flexDirection: 'column', gap: 8 }}>
            <Wrench size={32} style={{ opacity: 0.2 }} />
            <span style={{ fontSize: 14 }}>Select a skill</span>
          </div>
        )}
      </div>
    </div>
  )
}
