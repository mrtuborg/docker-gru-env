import { useEffect, useState } from 'react'
import { Eye, EyeOff, ExternalLink, Plus, Trash2 } from 'lucide-react'

interface Field {
  key: string
  label: string
  type: 'text' | 'password' | 'number' | 'checkbox' | 'select' | 'textarea' | 'taglist' | 'model-list'
  placeholder?: string
  hint?: string
  required?: boolean
  options?: { value: string; label: string }[]
  defaultValue?: string | number | boolean
  mono?: boolean
  showWhen?: { field: string; value: string }
  /** If true, shown in the wizard (connect) phase. All fields appear in settings. */
  wizard?: boolean
}

const PLUGIN_FIELDS: Record<string, Field[]> = {
  github: [
    // Wizard: just the host — auth is handled by OAuth redirect
    { key: 'host', label: 'GitHub Host', type: 'text', placeholder: 'github.com',
      hint: 'Hostname only — no https://. Use GHE hostname for enterprise.', defaultValue: 'github.com', required: true, wizard: true },
    // Settings: board and repos
    { key: 'board_url',     label: 'Project Board URL',  type: 'text',   placeholder: 'https://github.com/orgs/myorg/projects/5',
      hint: 'Full URL of the GitHub Projects v2 board. Owner and number are parsed automatically.' },
    { key: 'data_repo',     label: 'Data Repository',    type: 'text',   placeholder: 'owner/repo',
      hint: 'Repo that stores session logs, cost reports, and attribution DB.' },
    // Pages
    { key: 'pages_repo',    label: 'Pages Repository',   type: 'text',   placeholder: 'owner/repo (optional)',
      hint: 'GitHub Pages repo for publishing the cost/session dashboard.' },
    { key: 'pages_branch',  label: 'Pages Branch',       type: 'text',   placeholder: 'main', defaultValue: 'main',
      hint: 'Branch to push the generated Pages site to.' },
    // Cost attribution
    { key: 'allowed_repos', label: 'Allowed Repositories', type: 'taglist', placeholder: 'owner/repo — press Enter to add',
      hint: 'Repos whose sessions are attributed to this project board. Defaults to [data_repo] if empty.' },
    { key: 'repo_aliases',  label: 'Repository Aliases', type: 'textarea', mono: true,
      placeholder: 'owner/repo-fork: owner/canonical-repo', hint: 'YAML map — forks/mirrors mapped to their canonical repo.' },
    { key: 'repo_projects', label: 'Repo → Project Map', type: 'textarea', mono: true,
      placeholder: 'owner/some-repo: 13', hint: 'YAML map — repos with no issue refs attributed to this project number.' },
  ],
  copilot: [
    // Wizard: essential workspace settings
    { key: 'working_dir',   label: 'Default Working Directory', type: 'text', placeholder: '/workspace',
      hint: 'Default cwd for Copilot sessions inside the container.', wizard: true },
    { key: 'board_dir',     label: 'Board Directory',    type: 'text',   placeholder: 'hil-stress', defaultValue: 'hil-stress',
      hint: 'Subdirectory inside the workspace repo that contains config.yml and stage handlers.', wizard: true },
    // Settings: advanced watcher config
    { key: 'extensions_dir', label: 'Extensions Directory', type: 'text', placeholder: '~/.config/copilot/extensions',
      hint: 'Path to custom Copilot extensions available inside the container.' },
    { key: 'linked_repos',  label: 'Linked Repositories', type: 'taglist', placeholder: 'name=https://github.com/owner/repo',
      hint: 'Extra repos to clone into the container. Format: name=url.' },
    { key: 'watcher_prompts_dir',            label: 'Prompts Directory',          type: 'text',   placeholder: './prompts',
      hint: 'Path (relative to board_dir) with custom stage handler .md files.' },
    { key: 'watcher_stage_order',            label: 'Stage Order',                type: 'text',   placeholder: 'Todo, In progress', defaultValue: 'Todo, In progress',
      hint: 'Comma-separated board column names processed in order by the watcher.' },
    { key: 'watcher_poll_interval',          label: 'Poll Interval (s)',          type: 'number', placeholder: '300',  defaultValue: 300,
      hint: 'Seconds to sleep between watcher cycles when no issues are ready.' },
    { key: 'watcher_max_issues',             label: 'Max Issues per Run',         type: 'number', placeholder: '50',   defaultValue: 50,
      hint: 'Safety cap on total issues processed per watcher run.' },
    { key: 'watcher_max_per_issue',          label: 'Max Attempts per Issue',     type: 'number', placeholder: '3',
      hint: 'Maximum retries per issue in one run (prevents infinite loops).' },
    { key: 'watcher_session_timeout_hours',  label: 'Session Timeout (h)',        type: 'number', placeholder: '2',
      hint: 'Hours before a running Copilot session is killed and marked failed.' },
    { key: 'watcher_pause_between_sessions', label: 'Pause Between Sessions (s)', type: 'number', placeholder: '0', defaultValue: 0,
      hint: 'Seconds to wait between consecutive Copilot sessions.' },
    { key: 'watcher_models', label: 'Model Fallback List', type: 'model-list',
      hint: 'Ordered list of models. Priority 1 = preferred; higher = cheaper fallback after 3 consecutive failures.' },
  ],
  azure: [
    // All azure fields are wizard fields — auth is the whole point
    { key: 'auth_method',     label: 'Auth Method',        type: 'select', required: true, defaultValue: 'sas_token', wizard: true,
      options: [
        { value: 'sas_token',         label: 'SAS Token (paste from Azure Portal)' },
        { value: 'service_principal', label: 'Service Principal (client ID + secret)' },
      ],
      hint: 'SAS Token: generate from Azure Portal → Storage Account → Shared access signature.' },
    { key: 'storage_account', label: 'Storage Account',    type: 'text',   placeholder: 'rmeswprod', required: true, wizard: true,
      hint: 'Azure Storage account name.' },
    { key: 'container',       label: 'Container Name',     type: 'text',   placeholder: 'firmware', wizard: true,
      hint: 'Default blob container name.' },
    { key: '_sas_portal_link', label: '', type: 'link-button' as any, wizard: true,
      showWhen: { field: 'auth_method', value: 'sas_token' },
      hint: 'In Azure Portal: select your storage account → "Shared access signature" in the left menu → set permissions and expiry → click "Generate SAS and connection string" → copy the SAS token.' },
    { key: 'sas_token',       label: 'SAS Token',          type: 'password', wizard: true,
      placeholder: '?sv=2022-11-02&ss=b&srt=sco&sp=rl&se=...',
      hint: 'Paste the full SAS token starting with "?sv=". Copy from the Azure Portal page above.',
      showWhen: { field: 'auth_method', value: 'sas_token' } },
    { key: 'tenant_id',       label: 'Tenant ID',          type: 'text',   placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', wizard: true,
      hint: 'Required for Service Principal auth only.', showWhen: { field: 'auth_method', value: 'service_principal' } },
    { key: 'client_id',       label: 'Client ID (App ID)', type: 'text',   placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', wizard: true,
      hint: 'Required for Service Principal auth only.', showWhen: { field: 'auth_method', value: 'service_principal' } },
    { key: 'client_secret',   label: 'Client Secret',      type: 'password', placeholder: '…', wizard: true,
      hint: 'Required for Service Principal auth only.', showWhen: { field: 'auth_method', value: 'service_principal' } },
  ],
  obsidian: [
    { key: 'board_path',  label: 'Board File Path', type: 'text', placeholder: '/path/to/kanban.md', required: true, wizard: true,
      hint: 'Absolute path to the Obsidian Kanban markdown file.' },
    { key: 'watch_column', label: 'Watch Column',   type: 'text', placeholder: 'Todo', required: true, wizard: true,
      hint: 'Column name to pick cards from for processing.' },
    { key: 'done_column',  label: 'Done Column',    type: 'text', placeholder: 'Done', wizard: true,
      hint: 'Column to move cards to after a session completes.' },
    { key: 'auto_mark_done', label: 'Auto-mark Done', type: 'checkbox', defaultValue: true,
      hint: 'Move card to Done column when the Copilot session finishes successfully.' },
    { key: 'dry_run', label: 'Dry Run', type: 'checkbox', defaultValue: false,
      hint: "Plan sessions but don't move cards or commit results." },
  ],
  docker: [
    { key: 'cw_image',           label: 'Docker Image',        type: 'text', placeholder: 'gru:local',        defaultValue: 'gru:local',        wizard: true,
      hint: 'Docker image used to run Copilot sessions.' },
    { key: 'cw_data_volume',     label: 'Data Volume',         type: 'text', placeholder: 'gru-data',         defaultValue: 'gru-data',
      hint: 'Named volume for Copilot data mounted at /data/copilot.' },
    { key: 'cw_logs_volume',     label: 'Logs Volume',         type: 'text', placeholder: 'gru-logs',         defaultValue: 'gru-logs',
      hint: 'Named volume where session logs are written.' },
    { key: 'cw_instruct_volume', label: 'Instructions Volume', type: 'text', placeholder: 'gru-instructions', defaultValue: 'gru-instructions',
      hint: 'Named volume for custom instruction files, mounted at /data/instructions.' },
    { key: 'cw_ssh_path',        label: 'SSH Keys Path',       type: 'text', placeholder: '~/.ssh',           defaultValue: '~/.ssh',
      hint: 'Host path mounted read-only as /root/.ssh inside the container.' },
  ],
}

function TagList({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const t = draft.trim()
    if (t && !value.includes(t)) onChange([...value, t])
    setDraft('')
  }
  return (
    <div>
      <div style={{ display:'flex', gap:6, marginBottom:6 }}>
        <input className="form-input" style={{ flex:1 }} value={draft} placeholder={placeholder}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }}/>
        <button type="button" className="btn btn-secondary" style={{ padding:'0 10px' }} onClick={add}><Plus size={13}/></button>
      </div>
      {value.map((v, i) => (
        <div key={i} style={{ display:'flex', alignItems:'center', gap:6, background:'var(--surface2)', borderRadius:5, padding:'4px 8px', fontSize:12, fontFamily:'monospace', marginBottom:4 }}>
          <span style={{ flex:1 }}>{v}</span>
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))} style={{ background:'none', border:'none', cursor:'pointer', color:'var(--muted)', padding:2 }}><Trash2 size={11}/></button>
        </div>
      ))}
    </div>
  )
}

const MODEL_OPTIONS = ['claude-sonnet-4-5','claude-opus-4-5','claude-haiku-4-5','gpt-4o','gpt-4o-mini','gpt-4-turbo']

function ModelList({ value, onChange }: { value: {model:string;priority:number}[]; onChange: (v: {model:string;priority:number}[]) => void }) {
  const add = () => onChange([...value, { model: MODEL_OPTIONS[0], priority: value.length + 1 }])
  const upd = (i: number, field: 'model'|'priority', val: any) =>
    onChange(value.map((m, j) => j === i ? { ...m, [field]: field === 'priority' ? Number(val) : val } : m))
  return (
    <div>
      {value.length === 0 && <div style={{ fontSize:12, color:'var(--muted)', marginBottom:8 }}>No models — watcher uses the server default.</div>}
      {value.map((m, i) => (
        <div key={i} style={{ display:'flex', gap:8, alignItems:'center', marginBottom:6 }}>
          <select className="form-input" style={{ flex:1, background:'var(--surface2)', color:'var(--text)' }} value={m.model} onChange={e => upd(i,'model',e.target.value)}>
            {MODEL_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
            {!MODEL_OPTIONS.includes(m.model) && <option value={m.model}>{m.model}</option>}
          </select>
          <input className="form-input" type="number" min={1} value={m.priority} onChange={e => upd(i,'priority',e.target.value)} style={{ width:72 }} title="Priority (1=preferred)"/>
          <button type="button" className="btn btn-ghost" style={{ padding:'4px 6px' }} onClick={() => onChange(value.filter((_,j)=>j!==i))}><Trash2 size={13}/></button>
        </div>
      ))}
      <div style={{ display:'flex', gap:8, alignItems:'center' }}>
        <button type="button" className="btn btn-secondary" style={{ fontSize:12, padding:'4px 10px' }} onClick={add}><Plus size={12}/> Add model</button>
        {value.length > 0 && <span style={{ fontSize:11, color:'var(--muted)' }}>Priority 1 = preferred, higher = fallback</span>}
      </div>
    </div>
  )
}

interface Props {
  pluginType: string
  initialValues?: Record<string, any>
  onChange: (values: Record<string, any>) => void
  /** 'wizard' shows only connection/auth fields; 'settings' (default) shows all fields */
  phase?: 'wizard' | 'settings'
}

export default function PluginConfigForm({ pluginType, initialValues = {}, onChange, phase = 'settings' }: Props) {
  const allFields = PLUGIN_FIELDS[pluginType] || []
  const fields = phase === 'wizard' ? allFields.filter(f => f.wizard) : allFields
  const mkDefaults = () => {
    const d: Record<string, any> = {}
    fields.forEach(f => {
      if (f.type === 'taglist' || f.type === 'model-list') d[f.key] = initialValues[f.key] ?? []
      else d[f.key] = initialValues[f.key] ?? f.defaultValue ?? (f.type === 'checkbox' ? false : '')
    })
    return d
  }
  const [values, setValues] = useState<Record<string, any>>(mkDefaults)
  const [showPw, setShowPw] = useState<Record<string, boolean>>({})

  // Emit defaults to parent on mount (and when pluginType changes via key)
  useEffect(() => {
    onChange(values)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key: string, val: any) => { const n = {...values, [key]: val}; setValues(n); onChange(n) }

  if (!fields.length) return <p style={{ color:'var(--muted)', fontSize:13 }}>No configuration required.</p>

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {fields.filter(f => {
        if (f.showWhen) return values[f.showWhen.field] === f.showWhen.value
        return true
      }).map(f => (
        <div key={f.key}>
          {f.type === 'checkbox' ? (
            <label style={{ display:'flex', alignItems:'flex-start', gap:10, cursor:'pointer' }}>
              <input type="checkbox" checked={!!values[f.key]} onChange={e => set(f.key, e.target.checked)}
                style={{ width:16, height:16, accentColor:'var(--accent)', cursor:'pointer', marginTop:1 }}/>
              <div>
                <span style={{ fontSize:13, fontWeight:500 }}>{f.label}</span>
                {f.hint && <div style={{ fontSize:11, color:'var(--muted)', marginTop:2 }}>{f.hint}</div>}
              </div>
            </label>
          ) : (
            <>
              <div className="form-label">{f.label}{f.required && <span style={{ color:'var(--red)', marginLeft:2 }}>*</span>}</div>
              {f.type === 'password' ? (
                <div style={{ position:'relative' }}>
                  <input className="form-input" type={showPw[f.key] ? 'text' : 'password'} value={values[f.key]}
                    onChange={e => set(f.key, e.target.value)} placeholder={f.placeholder} style={{ paddingRight:36 }}/>
                  <button type="button" onClick={() => setShowPw(s=>({...s,[f.key]:!s[f.key]}))}
                    style={{ position:'absolute', right:8, top:'50%', transform:'translateY(-50%)', background:'none', border:'none', cursor:'pointer', color:'var(--muted)' }}>
                    {showPw[f.key] ? <EyeOff size={14}/> : <Eye size={14}/>}
                  </button>
                </div>
              ) : f.type === 'textarea' ? (
                <textarea className="form-input" value={values[f.key]} onChange={e => set(f.key, e.target.value)}
                  placeholder={f.placeholder} rows={4} style={{ resize:'vertical', fontFamily: f.mono ? 'monospace' : 'inherit', fontSize: f.mono ? 12 : 'inherit' }}/>
              ) : f.type === 'taglist' ? (
                <TagList value={values[f.key] || []} onChange={v => set(f.key, v)} placeholder={f.placeholder}/>
              ) : f.type === 'model-list' ? (
                <ModelList value={values[f.key] || []} onChange={v => set(f.key, v)}/>
              ) : f.type === 'select' ? (
                <select className="form-input" value={values[f.key]} onChange={e => set(f.key, e.target.value)} style={{ background:'var(--surface2)', color:'var(--text)' }}>
                  {f.options?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ) : (f.type as string) === 'link-button' ? (
                (() => {
                  const acct = values['storage_account']
                  // Link to the specific storage account if name is known, else to the accounts list
                  const url = acct
                    ? `https://portal.azure.com/#view/Microsoft_Azure_Storage/StorageMenuBlade/~/sas/storageAccountId/${encodeURIComponent(`/providers/Microsoft.Storage/storageAccounts/${acct}`)}`
                    : 'https://portal.azure.com/#view/HubsExtension/BrowseResource/resourceType/Microsoft.Storage%2FstorageAccounts'
                  return (
                    <a href={url} target="_blank" rel="noopener noreferrer"
                      style={{ display:'inline-flex', alignItems:'center', gap:6, padding:'8px 14px',
                        background:'var(--accent)', color:'#fff', borderRadius:6, fontSize:13,
                        fontWeight:500, textDecoration:'none', width:'fit-content' }}>
                      <ExternalLink size={14}/> Open Azure Portal → Shared access signature
                    </a>
                  )
                })()
              ) : (
                <input className="form-input" type={f.type} value={values[f.key]}
                  onChange={e => set(f.key, f.type==='number' ? Number(e.target.value) : e.target.value)}
                  placeholder={f.placeholder}/>
              )}
              {f.hint && <div style={{ fontSize:11, color:'var(--muted)', marginTop:3 }}>{f.hint}</div>}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
