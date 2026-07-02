import { useEffect, useState } from 'react'
import { Eye, EyeOff, ExternalLink, Plus, Trash2 } from 'lucide-react'

interface Field {
  key: string
  label: string
  type: 'text' | 'password' | 'number' | 'checkbox' | 'select' | 'textarea' | 'taglist' | 'model-list'
  placeholder?: string
  hint?: string | ((values: Record<string, any>) => string)
  required?: boolean
  options?: { value: string; label: string }[]
  defaultValue?: string | number | boolean
  mono?: boolean
  showWhen?: { field: string; value: string } | { field: string; values: string[] }
  /** If true, shown in the wizard (connect) phase. All fields appear in settings. */
  wizard?: boolean
}

const PLUGIN_FIELDS: Record<string, Field[]> = {
  github: [
    { key: 'host', label: 'GitHub Host', type: 'text', placeholder: 'github.com',
      hint: 'Hostname only — no https://. Use GHE hostname for enterprise.', defaultValue: 'github.com', required: true, wizard: true },
    { key: 'board_url', label: 'Project Board URL', type: 'text', placeholder: 'https://github.com/orgs/myorg/projects/5',
      hint: 'Full URL of the GitHub Projects v2 board. Owner and number are parsed automatically.' },
    { key: 'token', label: 'Personal Access Token (PAT)', type: 'password', placeholder: 'ghp_… or ghs_…',
      hint: 'Classic PAT with repo, project, read:org scopes. Leave blank to use OAuth device flow instead.' },
    { key: 'data_repo', label: 'Data Repository', type: 'text', placeholder: 'owner/repo',
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
    { key: 'github_connector_id', label: 'GitHub Connector ID', type: 'text', placeholder: 'github-main', wizard: true,
      hint: 'ID of the GitHub connector to use for authentication. Leave blank to auto-discover the first GitHub connector.' },
  ],
  azure: [
    { key: 'storage_account', label: 'Storage Account', type: 'text', placeholder: 'rmeswprod', required: true, wizard: true,
      hint: 'Azure Storage account name. Run: az storage account list --query "[].name" -o tsv' },
    { key: 'container', label: 'Container Name', type: 'text', placeholder: 'artifacts', wizard: true,
      hint: (v) => `Blob container. Run: az storage container list --account-name ${v.storage_account || '<account>'} --auth-mode login --query "[].name" -o tsv` },
  ],
  obsidian: [
    { key: 'email',      label: 'Obsidian Account Email',    type: 'text',     required: true, wizard: true,
      hint: 'The email address for your Obsidian account (obsidian.md).' },
    { key: 'password',   label: 'Obsidian Account Password', type: 'password', required: true, wizard: true,
      hint: 'Your Obsidian account password. Stored encrypted in the secure vault.' },
    { key: 'vault_name', label: 'Remote Vault Name',         type: 'text',     required: true, wizard: true,
      hint: 'Exact name of your remote vault as shown in Obsidian Sync settings.' },
    { key: 'board_path', label: 'Kanban Board File',         type: 'text',     required: true, wizard: true,
      placeholder: 'Projects/tasks.md',
      hint: 'Path to the Kanban .md file relative to the vault root (e.g. Projects/tasks.md).' },
    { key: 'watch_column',   label: 'Watch Column',   type: 'text',     placeholder: 'Todo',  defaultValue: 'Todo',
      hint: 'Board column name to pick cards from for processing.' },
    { key: 'poll_interval',  label: 'Sync Interval (s)', type: 'number', placeholder: '300', defaultValue: 300,
      hint: 'How often (seconds) to pull the latest vault changes from Obsidian Sync.' },
    { key: 'auto_mark_done', label: 'Auto-mark Done', type: 'checkbox', defaultValue: false,
      hint: 'Move card to Done column when the Copilot session finishes successfully.' },
    { key: 'dry_run',        label: 'Dry Run',        type: 'checkbox', defaultValue: false,
      hint: "Sync and parse the board but don't trigger any Copilot sessions." },
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
  analytics: [
    { key: 'host',     label: 'PostgreSQL Host / IP', type: 'text',   placeholder: '192.168.1.100 or hostname', required: true, wizard: true,
      hint: 'Hostname or IP of the PostgreSQL server. Use Docker container name for containers on the same network.' },
    { key: 'port',     label: 'Port',                type: 'number', placeholder: '5432',           defaultValue: 5432,            wizard: true },
    { key: 'database', label: 'Database',            type: 'text',   placeholder: 'gru_analytics',  defaultValue: 'gru_analytics', wizard: true },
    { key: 'user',     label: 'User',                type: 'text',   placeholder: 'gru',             defaultValue: 'gru',           wizard: true,
      hint: 'PostgreSQL user. No password required — server uses trust authentication.' },
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
  connectorType: string
  initialValues?: Record<string, any>
  onChange: (values: Record<string, any>) => void
  /** 'wizard' shows only connection/auth fields; 'settings' (default) shows all fields */
  phase?: 'wizard' | 'settings'
}

export default function ConnectorConfigForm({ connectorType, initialValues = {}, onChange, phase = 'settings' }: Props) {
  const allFields = PLUGIN_FIELDS[connectorType] || []
  const fields = phase === 'wizard' ? allFields.filter(f => f.wizard) : allFields

  // For GitHub: synthesize board_url from host+owner+number if not explicitly stored (legacy configs)
  const enriched = { ...initialValues }
  if (connectorType === 'github' && !enriched.board_url && enriched.project_owner && enriched.project_number) {
    const host = enriched.host || 'github.com'
    enriched.board_url = `https://${host}/orgs/${enriched.project_owner}/projects/${enriched.project_number}`
  }

  const mkDefaults = () => {
    const d: Record<string, any> = {}
    fields.forEach(f => {
      if (f.type === 'taglist' || f.type === 'model-list') d[f.key] = enriched[f.key] ?? []
      else d[f.key] = enriched[f.key] ?? f.defaultValue ?? (f.type === 'checkbox' ? false : '')
    })
    return d
  }
  const [values, setValues] = useState<Record<string, any>>(mkDefaults)
  const [showPw, setShowPw] = useState<Record<string, boolean>>({})

  // Emit defaults to parent on mount (and when connectorType changes via key)
  useEffect(() => {
    onChange(values)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (key: string, val: any) => { const n = {...values, [key]: val}; setValues(n); onChange(n) }

  if (!fields.length) return <p style={{ color:'var(--muted)', fontSize:13 }}>No configuration required.</p>

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      {fields.filter(f => {
        if (f.showWhen) {
          const sw = f.showWhen as any
          if ('values' in sw) return (sw.values as string[]).includes(values[sw.field])
          return values[sw.field] === sw.value
        }
        return true
      }).map(f => (
        <div key={f.key}>
          {f.type === 'checkbox' ? (
            <label style={{ display:'flex', alignItems:'flex-start', gap:10, cursor:'pointer' }}>
              <input type="checkbox" checked={!!values[f.key]} onChange={e => set(f.key, e.target.checked)}
                style={{ width:16, height:16, accentColor:'var(--accent)', cursor:'pointer', marginTop:1 }}/>
              <div>
                <span style={{ fontSize:13, fontWeight:500 }}>{f.label}</span>
                {f.hint && <div style={{ fontSize:11, color:'var(--muted)', marginTop:2 }}>{typeof f.hint === 'function' ? f.hint(values) : f.hint}</div>}
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
                  const sub  = values['subscription_id']
                  const rg   = values['resource_group']
                  const url = (acct && sub && rg)
                    ? `https://portal.azure.com/#resource/subscriptions/${sub}/resourceGroups/${rg}/providers/Microsoft.Storage/storageAccounts/${acct}/sas`
                    : 'https://portal.azure.com'
                  return (
                    <a href={url} target="_blank" rel="noopener noreferrer"
                      style={{ display:'inline-flex', alignItems:'center', gap:6, padding:'8px 14px',
                        background:'var(--accent)', color:'#fff', borderRadius:6, fontSize:13,
                        fontWeight:500, textDecoration:'none', width:'fit-content' }}>
                      <ExternalLink size={14}/> Open Shared access signature in Azure Portal
                    </a>
                  )
                })()
              ) : (
                <input className="form-input" type={f.type} value={values[f.key]}
                  onChange={e => set(f.key, f.type==='number' ? Number(e.target.value) : e.target.value)}
                  placeholder={f.placeholder}/>
              )}
              {f.hint && <div style={{ fontSize:11, color:'var(--muted)', marginTop:3 }}>{typeof f.hint === 'function' ? f.hint(values) : f.hint}</div>}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
