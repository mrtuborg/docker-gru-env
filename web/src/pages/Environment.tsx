import { useState, useEffect, useCallback } from 'react'
import { Plus, Trash2, Save, Eye, EyeOff, Upload, Download, FileText, Key, Variable, RefreshCw } from 'lucide-react'

interface EnvVar {
  name: string
  value: string
  description: string
  updated_at: string
}

interface EnvSecret {
  name: string
  description: string
  updated_at: string
}

interface EnvFile {
  name: string
  size: number
  updated_at: string
}

const EMPTY_VAR: EnvVar = { name: '', value: '', description: '', updated_at: '' }
const EMPTY_SECRET: EnvSecret & { value?: string } = { name: '', description: '', updated_at: '' }

export default function Environment() {
  const [vars, setVars] = useState<EnvVar[]>([])
  const [secrets, setSecrets] = useState<EnvSecret[]>([])
  const [files, setFiles] = useState<EnvFile[]>([])

  const [editingVar, setEditingVar] = useState<EnvVar | null>(null)
  const [editingSecret, setEditingSecret] = useState<(EnvSecret & { value?: string }) | null>(null)
  const [secretVisible, setSecretVisible] = useState(false)

  const [filePreview, setFilePreview] = useState<{ name: string; content: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const load = useCallback(() => {
    fetch('/api/env/variables').then(r => r.json()).then(d => setVars(Array.isArray(d) ? d : [])).catch(() => {})
    fetch('/api/env/secrets').then(r => r.json()).then(d => setSecrets(Array.isArray(d) ? d : [])).catch(() => {})
    fetch('/api/env/files').then(r => r.json()).then(d => setFiles(Array.isArray(d) ? d : [])).catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  function flash(text: string, ok = true) { setMsg({ text, ok }); setTimeout(() => setMsg(null), 3000) }

  // ── Variables ──────────────────────────────────────────────────────────────

  async function saveVar() {
    if (!editingVar?.name) return
    setSaving(true)
    const r = await fetch(`/api/env/variables/${encodeURIComponent(editingVar.name)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editingVar),
    })
    setSaving(false)
    if (r.ok) { flash('Variable saved'); setEditingVar(null); load() }
    else flash('Save failed', false)
  }

  async function deleteVar(name: string) {
    await fetch(`/api/env/variables/${encodeURIComponent(name)}`, { method: 'DELETE' })
    load()
  }

  // ── Secrets ────────────────────────────────────────────────────────────────

  async function saveSecret() {
    if (!editingSecret?.name || !editingSecret.value) return
    setSaving(true)
    const r = await fetch(`/api/env/secrets/${encodeURIComponent(editingSecret.name)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editingSecret),
    })
    setSaving(false)
    if (r.ok) { flash('Secret saved'); setEditingSecret(null); load() }
    else flash('Save failed', false)
  }

  async function deleteSecret(name: string) {
    await fetch(`/api/env/secrets/${encodeURIComponent(name)}`, { method: 'DELETE' })
    load()
  }

  // ── Files ──────────────────────────────────────────────────────────────────

  async function uploadFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; e.target.value = ''
    if (!file) return
    const fd = new FormData(); fd.append('file', file)
    const r = await fetch('/api/env/files/upload', { method: 'POST', body: fd })
    if (r.ok) { flash(`Uploaded ${file.name}`); load() }
    else flash('Upload failed', false)
  }

  async function previewFile(name: string) {
    const r = await fetch(`/api/env/files/${encodeURIComponent(name)}`)
    if (r.ok) { const d = await r.json(); setFilePreview({ name, content: d.content }) }
  }

  async function deleteFile(name: string) {
    await fetch(`/api/env/files/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (filePreview?.name === name) setFilePreview(null)
    load()
  }

  function fmtBytes(n: number) {
    if (n < 1024) return `${n} B`
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
    return `${(n / 1024 / 1024).toFixed(1)} MB`
  }

  function fmtDate(s: string) { return s ? new Date(s).toLocaleDateString() : '' }

  const section = (icon: React.ReactNode, title: string, subtitle: string) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
      <div style={{ color: 'var(--blue)' }}>{icon}</div>
      <div>
        <div style={{ fontWeight: 700, fontSize: 14 }}>{title}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>{subtitle}</div>
      </div>
    </div>
  )

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>Environment</h2>
        <button className="btn btn-ghost" style={{ marginLeft: 'auto', gap: 6 }} onClick={load}>
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {msg && (
        <div style={{ padding: '8px 14px', borderRadius: 6, marginBottom: 16, fontSize: 13,
          background: msg.ok ? 'color-mix(in srgb, var(--green) 12%, transparent)' : 'color-mix(in srgb, var(--red) 12%, transparent)',
          color: msg.ok ? 'var(--green)' : 'var(--red)', border: `1px solid ${msg.ok ? 'var(--green)' : 'var(--red)'}20` }}>
          {msg.text}
        </div>
      )}

      {/* ── Variables ─────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 20 }}>
        {section(<Variable size={18} />, 'Variables', 'Key-value pairs injected as environment variables into every skill and pipeline run')}

        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted)', textAlign: 'left' }}>
              <th style={{ padding: '4px 8px' }}>Name</th>
              <th style={{ padding: '4px 8px' }}>Value</th>
              <th style={{ padding: '4px 8px' }}>Description</th>
              <th style={{ padding: '4px 8px' }}>Updated</th>
              <th style={{ padding: '4px 8px', width: 60 }}></th>
            </tr>
          </thead>
          <tbody>
            {vars.map(v => (
              <tr key={v.name} style={{ borderBottom: '1px solid var(--border)10' }}
                onClick={() => setEditingVar({ ...v })}
                className="table-row-hover">
                <td style={{ padding: '6px 8px', fontFamily: 'ui-monospace, monospace', color: 'var(--blue)' }}>{v.name}</td>
                <td style={{ padding: '6px 8px', fontFamily: 'ui-monospace, monospace', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v.value}</td>
                <td style={{ padding: '6px 8px', color: 'var(--muted)' }}>{v.description}</td>
                <td style={{ padding: '6px 8px', color: 'var(--muted)', fontSize: 11 }}>{fmtDate(v.updated_at)}</td>
                <td style={{ padding: '6px 8px' }}>
                  <button className="btn btn-ghost" style={{ padding: '2px 6px', color: 'var(--red)' }}
                    onClick={e => { e.stopPropagation(); deleteVar(v.name) }}><Trash2 size={11} /></button>
                </td>
              </tr>
            ))}
            {vars.length === 0 && (
              <tr><td colSpan={5} style={{ padding: '12px 8px', color: 'var(--muted)', fontStyle: 'italic' }}>No variables yet</td></tr>
            )}
          </tbody>
        </table>

        <button className="btn btn-ghost" style={{ marginTop: 10, gap: 6, fontSize: 12 }}
          onClick={() => setEditingVar({ ...EMPTY_VAR })}>
          <Plus size={12} /> Add variable
        </button>

        {editingVar && (
          <div style={{ marginTop: 14, padding: 16, borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Name</label>
                <input className="input" style={{ fontFamily: 'ui-monospace, monospace' }} placeholder="MY_VARIABLE"
                  value={editingVar.name} onChange={e => setEditingVar(v => ({ ...v!, name: e.target.value }))} />
              </div>
              <div style={{ flex: 2 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Value</label>
                <input className="input" placeholder="value"
                  value={editingVar.value} onChange={e => setEditingVar(v => ({ ...v!, value: e.target.value }))} />
              </div>
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Description (optional)</label>
              <input className="input" placeholder="What is this variable for?"
                value={editingVar.description} onChange={e => setEditingVar(v => ({ ...v!, description: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setEditingVar(null)}>Cancel</button>
              <button className="btn btn-primary" style={{ gap: 6 }} onClick={saveVar} disabled={!editingVar.name || saving}>
                <Save size={12} /> {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Secrets ───────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 20 }}>
        {section(<Key size={18} />, 'Secrets', 'Encrypted values — injected as env vars but never shown in the UI after saving')}

        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted)', textAlign: 'left' }}>
              <th style={{ padding: '4px 8px' }}>Name</th>
              <th style={{ padding: '4px 8px' }}>Description</th>
              <th style={{ padding: '4px 8px' }}>Updated</th>
              <th style={{ padding: '4px 8px', width: 100 }}></th>
            </tr>
          </thead>
          <tbody>
            {secrets.map(s => (
              <tr key={s.name} style={{ borderBottom: '1px solid var(--border)10' }}
                onClick={() => { setEditingSecret({ ...s, value: '' }); setSecretVisible(false) }}
                className="table-row-hover">
                <td style={{ padding: '6px 8px', fontFamily: 'ui-monospace, monospace', color: 'var(--yellow)' }}>{s.name}</td>
                <td style={{ padding: '6px 8px', color: 'var(--muted)' }}>{s.description}</td>
                <td style={{ padding: '6px 8px', color: 'var(--muted)', fontSize: 11 }}>{fmtDate(s.updated_at)}</td>
                <td style={{ padding: '6px 8px' }}>
                  <span style={{ fontSize: 11, color: 'var(--muted)', marginRight: 6 }}>••••••••</span>
                  <button className="btn btn-ghost" style={{ padding: '2px 6px', color: 'var(--red)' }}
                    onClick={e => { e.stopPropagation(); deleteSecret(s.name) }}><Trash2 size={11} /></button>
                </td>
              </tr>
            ))}
            {secrets.length === 0 && (
              <tr><td colSpan={4} style={{ padding: '12px 8px', color: 'var(--muted)', fontStyle: 'italic' }}>No secrets yet</td></tr>
            )}
          </tbody>
        </table>

        <button className="btn btn-ghost" style={{ marginTop: 10, gap: 6, fontSize: 12 }}
          onClick={() => { setEditingSecret({ ...EMPTY_SECRET, value: '' }); setSecretVisible(false) }}>
          <Plus size={12} /> Add secret
        </button>

        {editingSecret && (
          <div style={{ marginTop: 14, padding: 16, borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Name</label>
                <input className="input" style={{ fontFamily: 'ui-monospace, monospace' }} placeholder="MY_SECRET"
                  value={editingSecret.name} onChange={e => setEditingSecret(v => ({ ...v!, name: e.target.value }))} />
              </div>
              <div style={{ flex: 2, position: 'relative' }}>
                <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Value</label>
                <input className="input" type={secretVisible ? 'text' : 'password'} placeholder="secret value"
                  value={editingSecret.value || ''} onChange={e => setEditingSecret(v => ({ ...v!, value: e.target.value }))} />
                <button className="btn btn-ghost" style={{ position: 'absolute', right: 6, top: 22, padding: '2px 4px' }}
                  onClick={() => setSecretVisible(v => !v)}>
                  {secretVisible ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>
              </div>
            </div>
            <div>
              <label style={{ fontSize: 11, color: 'var(--muted)', display: 'block', marginBottom: 3 }}>Description (optional)</label>
              <input className="input" placeholder="What is this secret for?"
                value={editingSecret.description} onChange={e => setEditingSecret(v => ({ ...v!, description: e.target.value }))} />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost" onClick={() => setEditingSecret(null)}>Cancel</button>
              <button className="btn btn-primary" style={{ gap: 6 }} onClick={saveSecret} disabled={!editingSecret.name || !editingSecret.value || saving}>
                <Save size={12} /> {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Files ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ padding: 20 }}>
        {section(<FileText size={18} />, 'Files', 'Upload YAML, CSV, or text files — accessible to skills at /data/gru/env/files/<name>')}

        <div style={{ display: 'flex', gap: 16 }}>
          {/* File list */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', color: 'var(--muted)', textAlign: 'left' }}>
                  <th style={{ padding: '4px 8px' }}>Name</th>
                  <th style={{ padding: '4px 8px' }}>Size</th>
                  <th style={{ padding: '4px 8px' }}>Updated</th>
                  <th style={{ padding: '4px 8px', width: 80 }}></th>
                </tr>
              </thead>
              <tbody>
                {files.map(f => (
                  <tr key={f.name} style={{ borderBottom: '1px solid var(--border)10', cursor: 'pointer',
                    background: filePreview?.name === f.name ? 'color-mix(in srgb, var(--blue) 6%, transparent)' : undefined }}
                    onClick={() => previewFile(f.name)}>
                    <td style={{ padding: '6px 8px', fontFamily: 'ui-monospace, monospace', color: 'var(--blue)', fontSize: 12 }}>{f.name}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--muted)', fontSize: 11 }}>{fmtBytes(f.size)}</td>
                    <td style={{ padding: '6px 8px', color: 'var(--muted)', fontSize: 11 }}>{fmtDate(f.updated_at)}</td>
                    <td style={{ padding: '6px 8px', display: 'flex', gap: 4 }}>
                      <a href={`/api/env/files/${encodeURIComponent(f.name)}/download`} download
                        onClick={e => e.stopPropagation()}
                        className="btn btn-ghost" style={{ padding: '2px 6px' }}><Download size={11} /></a>
                      <button className="btn btn-ghost" style={{ padding: '2px 6px', color: 'var(--red)' }}
                        onClick={e => { e.stopPropagation(); deleteFile(f.name) }}><Trash2 size={11} /></button>
                    </td>
                  </tr>
                ))}
                {files.length === 0 && (
                  <tr><td colSpan={4} style={{ padding: '12px 8px', color: 'var(--muted)', fontStyle: 'italic' }}>No files uploaded yet</td></tr>
                )}
              </tbody>
            </table>

            <label className="btn btn-ghost" style={{ marginTop: 10, gap: 6, fontSize: 12, cursor: 'pointer', display: 'inline-flex', alignItems: 'center' }}>
              <Upload size={12} /> Upload file
              <input type="file" style={{ display: 'none' }} onChange={uploadFile} />
            </label>
          </div>

          {/* File preview */}
          {filePreview && (
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 600, fontFamily: 'ui-monospace, monospace' }}>{filePreview.name}</span>
                <button className="btn btn-ghost" style={{ padding: '2px 6px', marginLeft: 'auto', fontSize: 11 }}
                  onClick={() => setFilePreview(null)}>✕</button>
              </div>
              <pre style={{ margin: 0, padding: 12, borderRadius: 6, background: 'var(--surface)',
                border: '1px solid var(--border)', fontSize: 11, fontFamily: 'ui-monospace, monospace',
                overflowX: 'auto', overflowY: 'auto', maxHeight: 320, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {filePreview.content}
              </pre>
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 16, fontSize: 11, color: 'var(--muted)', lineHeight: 1.6 }}>
        💡 <strong>Variables and secrets</strong> are injected into every skill subprocess as environment variables.<br />
        💡 <strong>Files</strong> are stored at <code>/data/gru/env/files/</code> inside the container — reference them in skills with <code>WORKSPACE_ENV_FILES=/data/gru/env/files</code>.
      </div>
    </div>
  )
}
