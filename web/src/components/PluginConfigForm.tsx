import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'

interface Field {
  key: string
  label: string
  type: 'text' | 'password' | 'number' | 'checkbox' | 'select'
  placeholder?: string
  hint?: string
  required?: boolean
  options?: { value: string; label: string }[]
  defaultValue?: string | number | boolean
}

const PLUGIN_FIELDS: Record<string, Field[]> = {
  github: [
    { key: 'host', label: 'GitHub Host', type: 'text', placeholder: 'github.com', hint: 'Use your GHE hostname for enterprise (e.g. sensio.ghe.com)', defaultValue: 'github.com' },
    { key: 'data_repo', label: 'Data Repository', type: 'text', placeholder: 'owner/repo', hint: 'Repo that stores session logs and cost reports', required: true },
    { key: 'project_owner', label: 'Project Owner', type: 'text', placeholder: 'owner or org', hint: 'GitHub user/org that owns the project board', required: true },
    { key: 'project_number', label: 'Project Number', type: 'number', placeholder: '1', hint: 'GitHub Projects v2 project number', required: true },
    { key: 'pages_repo', label: 'Pages Repository', type: 'text', placeholder: 'owner/repo (optional)', hint: 'Repo with GitHub Pages for dashboard publishing' },
    { key: 'token', label: 'Personal Access Token', type: 'password', placeholder: 'ghp_… or leave blank to use OAuth', hint: 'Classic PAT with repo + project scopes. Leave blank to authorize via OAuth.' },
  ],
  copilot: [
    { key: 'working_dir', label: 'Default Working Directory', type: 'text', placeholder: '/workspace', hint: 'Default cwd for Copilot sessions (can be overridden per board)' },
    { key: 'model', label: 'Default Model', type: 'select', options: [
      { value: '', label: 'Server default' },
      { value: 'claude-sonnet-4-5', label: 'Claude Sonnet 4.5' },
      { value: 'claude-opus-4-5', label: 'Claude Opus 4.5' },
      { value: 'gpt-4o', label: 'GPT-4o' },
    ], hint: 'LLM model for Copilot sessions' },
    { key: 'extensions_dir', label: 'Extensions Directory', type: 'text', placeholder: '~/.config/copilot/extensions', hint: 'Path to custom Copilot extensions' },
  ],
  azure: [
    { key: 'tenant_id', label: 'Tenant ID', type: 'text', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', required: true, hint: 'Azure Active Directory tenant ID' },
    { key: 'client_id', label: 'Client ID (App ID)', type: 'text', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', required: true, hint: 'Service principal / app registration client ID' },
    { key: 'client_secret', label: 'Client Secret', type: 'password', placeholder: '…', required: true, hint: 'Service principal client secret' },
    { key: 'storage_account', label: 'Storage Account', type: 'text', placeholder: 'mystorageaccount', required: true, hint: 'Azure Storage account name' },
    { key: 'container_name', label: 'Container Name', type: 'text', placeholder: 'firmware', hint: 'Default blob container name' },
  ],
  obsidian: [
    { key: 'board_path', label: 'Board File Path', type: 'text', placeholder: '/path/to/kanban.md', required: true, hint: 'Absolute path to the Obsidian Kanban markdown file' },
    { key: 'watch_column', label: 'Watch Column', type: 'text', placeholder: 'Todo', required: true, hint: 'Column name to pick cards from' },
    { key: 'done_column', label: 'Done Column', type: 'text', placeholder: 'Done', hint: 'Column to move cards to after completion' },
    { key: 'auto_mark_done', label: 'Auto-mark Done', type: 'checkbox', hint: 'Move card to Done column after a session completes', defaultValue: true },
    { key: 'dry_run', label: 'Dry Run', type: 'checkbox', hint: "Plan sessions but don't move cards or commit results", defaultValue: false },
  ],
}

interface Props {
  pluginType: string
  pluginId?: string        // for edit mode
  initialValues?: Record<string, any>
  onChange: (values: Record<string, any>) => void
}

export default function PluginConfigForm({ pluginType, initialValues = {}, onChange }: Props) {
  const fields = PLUGIN_FIELDS[pluginType] || []
  const defaults: Record<string, any> = {}
  fields.forEach(f => {
    defaults[f.key] = initialValues[f.key] ?? f.defaultValue ?? (f.type === 'checkbox' ? false : '')
  })
  const [values, setValues] = useState<Record<string, any>>(defaults)
  const [showPassword, setShowPassword] = useState<Record<string, boolean>>({})

  const set = (key: string, val: any) => {
    const next = { ...values, [key]: val }
    setValues(next)
    onChange(next)
  }

  if (fields.length === 0) {
    return <p style={{ color:'var(--muted)', fontSize:13 }}>No configuration required for this plugin type.</p>
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      {fields.map(f => (
        <div key={f.key}>
          {f.type === 'checkbox' ? (
            <label style={{ display:'flex', alignItems:'center', gap:10, cursor:'pointer' }}>
              <input
                type="checkbox"
                checked={!!values[f.key]}
                onChange={e => set(f.key, e.target.checked)}
                style={{ width:16, height:16, accentColor:'var(--accent)', cursor:'pointer' }}
              />
              <span style={{ fontSize:13, fontWeight:500 }}>{f.label}</span>
              {f.hint && <span style={{ fontSize:11, color:'var(--muted)' }}>— {f.hint}</span>}
            </label>
          ) : (
            <>
              <div className="form-label">
                {f.label}{f.required && <span style={{ color:'var(--red)', marginLeft:2 }}>*</span>}
              </div>
              {f.type === 'select' ? (
                <select
                  className="form-input"
                  value={values[f.key]}
                  onChange={e => set(f.key, e.target.value)}
                  style={{ background:'var(--surface2)', color:'var(--text)' }}
                >
                  {f.options?.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              ) : f.type === 'password' ? (
                <div style={{ position:'relative' }}>
                  <input
                    className="form-input"
                    type={showPassword[f.key] ? 'text' : 'password'}
                    value={values[f.key]}
                    onChange={e => set(f.key, e.target.value)}
                    placeholder={f.placeholder}
                    style={{ paddingRight:36 }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(s => ({ ...s, [f.key]: !s[f.key] }))}
                    style={{ position:'absolute', right:8, top:'50%', transform:'translateY(-50%)', background:'none', border:'none', cursor:'pointer', color:'var(--muted)', padding:2 }}
                  >
                    {showPassword[f.key] ? <EyeOff size={14}/> : <Eye size={14}/>}
                  </button>
                </div>
              ) : (
                <input
                  className="form-input"
                  type={f.type}
                  value={values[f.key]}
                  onChange={e => set(f.key, f.type === 'number' ? Number(e.target.value) : e.target.value)}
                  placeholder={f.placeholder}
                />
              )}
              {f.hint && <div style={{ fontSize:11, color:'var(--muted)', marginTop:3 }}>{f.hint}</div>}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
