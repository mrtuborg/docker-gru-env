import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Save, Plus, Trash2, Bot, User, ChevronDown, ChevronRight,
  Upload, RefreshCw, GripVertical,
} from 'lucide-react'

interface Stage {
  column: string
  actor: string
  prompt: string
  on_success: string
  on_failure: string
  on_timeout: string
  env: Record<string, string>
}

interface ModelConfig {
  model: string
  priority: number
}

interface FindingsBoard {
  project_owner: string
  project_number: number
  initial_status: string
}

interface PipelineData {
  id: string
  name: string
  enabled: boolean | number
  plugin_id: string
  board_type: string
  project_owner: string
  project_number: number
  board_path: string | null
  stages: (Stage & { column_name?: string; stage_index?: number; env_json?: string })[]
  poll_interval: number
  max_issues: number
  max_retries: number
  session_timeout_hours: number
  models: ModelConfig[]
  allowed_repos: string[]
  findings: FindingsBoard | null
}

const TEMPLATE_VARS = [
  '${ISSUE_NUM}', '${REPO}', '${ISSUE_REPO}', '${ISSUE_STAGE}',
  '${GH_HOST}', '${PROJECT_NUM}', '${PROJECT_OWNER}', '${PROJECT_ID}',
  '${PROJECT_ENTITY}', '${ALLOWED_REPOS}',
]

const DEFAULT_MODELS = [
  'claude-sonnet-4.6', 'claude-haiku-4.5', 'claude-opus-4.8',
  'gpt-5.5', 'gpt-5-mini',
]

function normalizeStage(s: any): Stage {
  return {
    column: s.column || s.column_name || '',
    actor: s.actor || 'ai',
    prompt: s.prompt || '',
    on_success: s.on_success || '',
    on_failure: s.on_failure || '',
    on_timeout: s.on_timeout || '',
    env: s.env || (s.env_json ? JSON.parse(s.env_json) : {}),
  }
}

export default function PipelineEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const isNew = id === 'new'

  const [pipeline, setPipeline] = useState<PipelineData | null>(null)
  const [selectedStage, setSelectedStage] = useState<number>(-1)
  const [saving, setSaving] = useState(false)
  const [plugins, setPlugins] = useState<any[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [fetchingColumns, setFetchingColumns] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    fetch('/api/plugins').then(r => r.json()).then(setPlugins).catch(() => {})

    if (isNew) {
      setPipeline({
        id: '', name: '', enabled: true, plugin_id: '', board_type: 'github',
        project_owner: '', project_number: 0, board_path: null,
        stages: [], poll_interval: 300, max_issues: 50, max_retries: 3,
        session_timeout_hours: 4, models: [], allowed_repos: [], findings: null,
      })
    } else {
      fetch(`/api/pipelines/${id}`).then(r => r.json()).then(data => {
        data.stages = (data.stages || []).map(normalizeStage)
        setPipeline(data)
        if (data.stages.length > 0) setSelectedStage(0)
      })
    }
  }, [id])

  const update = useCallback((patch: Partial<PipelineData>) => {
    setPipeline(prev => prev ? { ...prev, ...patch } : prev)
    setDirty(true)
  }, [])

  const updateStage = useCallback((idx: number, patch: Partial<Stage>) => {
    setPipeline(prev => {
      if (!prev) return prev
      const stages = [...prev.stages]
      stages[idx] = { ...stages[idx], ...patch }
      return { ...prev, stages }
    })
    setDirty(true)
  }, [])

  const addStage = () => {
    if (!pipeline) return
    const newStage: Stage = {
      column: '', actor: 'ai', prompt: '', on_success: '', on_failure: '', on_timeout: '', env: {},
    }
    update({ stages: [...pipeline.stages, newStage] })
    setSelectedStage(pipeline.stages.length)
  }

  const removeStage = (idx: number) => {
    if (!pipeline) return
    const stages = pipeline.stages.filter((_, i) => i !== idx)
    update({ stages })
    setSelectedStage(Math.min(selectedStage, stages.length - 1))
  }

  const fetchColumns = async () => {
    if (!pipeline?.plugin_id || !pipeline.project_owner || !pipeline.project_number) return
    setFetchingColumns(true)
    try {
      const resp = await fetch(
        `/api/pipelines/board-columns/${pipeline.plugin_id}?owner=${pipeline.project_owner}&number=${pipeline.project_number}`
      )
      if (!resp.ok) throw new Error(await resp.text())
      const { columns } = await resp.json()
      // Auto-create stages from columns
      const stages: Stage[] = columns.map((col: string) => ({
        column: col,
        actor: ['Review', 'Done', 'Backlog'].some(h => col.toLowerCase().includes(h.toLowerCase())) ? 'human' : 'ai',
        prompt: '', on_success: '', on_failure: '', on_timeout: '', env: {},
      }))
      update({ stages })
      if (stages.length > 0) setSelectedStage(0)
    } catch (e: any) {
      alert('Failed to fetch columns: ' + e.message)
    } finally {
      setFetchingColumns(false)
    }
  }

  const save = async () => {
    if (!pipeline) return
    setSaving(true)
    try {
      const method = isNew ? 'POST' : 'PUT'
      const url = isNew ? '/api/pipelines' : `/api/pipelines/${pipeline.id}`
      const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pipeline),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Save failed' }))
        throw new Error(err.detail || 'Save failed')
      }
      const saved = await resp.json()
      setDirty(false)
      if (isNew) navigate(`/pipelines/${saved.id}`, { replace: true })
    } catch (e: any) {
      alert(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!pipeline) return (
    <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)', padding:48 }}>
      <div className="spinner"/> Loading…
    </div>
  )

  const sel = selectedStage >= 0 && selectedStage < pipeline.stages.length
    ? pipeline.stages[selectedStage]
    : null

  const githubPlugins = plugins.filter(p => p.plugin_type === 'github')

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'calc(100vh - 56px - 48px)' }}>
      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', gap:12,
        padding:'0 0 16px', borderBottom:'1px solid var(--border)', marginBottom:16,
      }}>
        <button className="btn btn-ghost" onClick={() => navigate('/pipelines')} style={{ padding:'6px 8px' }}>
          <ArrowLeft size={16}/>
        </button>
        <h1 style={{ fontSize:18, fontWeight:700, flex:1 }}>
          {isNew ? 'New Pipeline' : pipeline.name}
        </h1>
        {dirty && <span style={{ fontSize:11, color:'var(--yellow)' }}>unsaved</span>}
        <button className="btn btn-primary" onClick={save} disabled={saving || !pipeline.id || !pipeline.name}>
          <Save size={14}/> {saving ? 'Saving…' : 'Save'}
        </button>
      </div>

      {/* Main content — two columns */}
      <div style={{ display:'flex', flex:1, gap:16, overflow:'hidden' }}>
        {/* Left: pipeline config + stages */}
        <div style={{ width:320, flexShrink:0, overflow:'auto', paddingRight:8 }}>
          {/* Basic info */}
          <div style={{ marginBottom:20 }}>
            <label className="form-label">Pipeline ID</label>
            <input className="form-input" value={pipeline.id} disabled={!isNew}
              onChange={e => update({ id: e.target.value })} placeholder="e.g. hil-stress"/>
          </div>
          <div style={{ marginBottom:20 }}>
            <label className="form-label">Name</label>
            <input className="form-input" value={pipeline.name}
              onChange={e => update({ name: e.target.value })} placeholder="RoomMate QA — Stress Testing"/>
          </div>

          {/* Board connection */}
          <div className="section-label" style={{ marginTop:20 }}>Board Connection</div>
          <div style={{ marginBottom:12 }}>
            <label className="form-label">Plugin</label>
            <select className="form-input" value={pipeline.plugin_id}
              onChange={e => update({ plugin_id: e.target.value })}>
              <option value="">Select plugin…</option>
              {githubPlugins.map(p => (
                <option key={p.id} value={p.id}>{p.display_name || p.id}</option>
              ))}
            </select>
          </div>
          <div style={{ display:'flex', gap:8, marginBottom:12 }}>
            <div style={{ flex:2 }}>
              <label className="form-label">Owner</label>
              <input className="form-input" value={pipeline.project_owner}
                onChange={e => update({ project_owner: e.target.value })} placeholder="org-name"/>
            </div>
            <div style={{ flex:1 }}>
              <label className="form-label">#</label>
              <input className="form-input" type="number" value={pipeline.project_number || ''}
                onChange={e => update({ project_number: parseInt(e.target.value) || 0 })}/>
            </div>
          </div>
          <button className="btn btn-secondary" style={{ width:'100%', marginBottom:20, fontSize:12 }}
            onClick={fetchColumns} disabled={fetchingColumns || !pipeline.plugin_id || !pipeline.project_owner || !pipeline.project_number}>
            {fetchingColumns ? <><div className="spinner" style={{ width:12, height:12 }}/> Fetching…</> :
              <><RefreshCw size={12}/> Fetch Board Columns</>}
          </button>

          {/* Stages */}
          <div className="section-label">Stages</div>
          <div style={{ display:'flex', flexDirection:'column', gap:4, marginBottom:12 }}>
            {pipeline.stages.map((stage, i) => (
              <div key={i}
                className={`card card-interactive ${selectedStage === i ? 'card-active' : ''}`}
                onClick={() => setSelectedStage(i)}
                style={{
                  padding:'8px 12px', display:'flex', alignItems:'center', gap:8, cursor:'pointer',
                }}>
                <div style={{ color:'var(--muted)', cursor:'grab' }}><GripVertical size={12}/></div>
                <div style={{
                  width:20, height:20, borderRadius:4, display:'flex', alignItems:'center', justifyContent:'center',
                  background: stage.actor === 'ai' ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'var(--surface2)',
                  color: stage.actor === 'ai' ? 'var(--accent)' : 'var(--muted)',
                }}>
                  {stage.actor === 'ai' ? <Bot size={12}/> : <User size={12}/>}
                </div>
                <span style={{ flex:1, fontSize:13, fontWeight:500 }}>{stage.column || '(unnamed)'}</span>
                {i > 0 && (
                  <button className="btn btn-ghost" style={{ padding:2 }}
                    onClick={e => { e.stopPropagation(); removeStage(i) }}>
                    <Trash2 size={12}/>
                  </button>
                )}
              </div>
            ))}
          </div>
          <button className="btn btn-secondary" style={{ width:'100%', fontSize:12 }} onClick={addStage}>
            <Plus size={12}/> Add Stage
          </button>

          {/* Settings */}
          <div className="section-label" style={{ marginTop:24 }}>Settings</div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:12 }}>
            <div>
              <label className="form-label">Poll (s)</label>
              <input className="form-input" type="number" value={pipeline.poll_interval}
                onChange={e => update({ poll_interval: parseInt(e.target.value) || 300 })}/>
            </div>
            <div>
              <label className="form-label">Max Issues</label>
              <input className="form-input" type="number" value={pipeline.max_issues}
                onChange={e => update({ max_issues: parseInt(e.target.value) || 50 })}/>
            </div>
            <div>
              <label className="form-label">Retries</label>
              <input className="form-input" type="number" value={pipeline.max_retries}
                onChange={e => update({ max_retries: parseInt(e.target.value) || 3 })}/>
            </div>
            <div>
              <label className="form-label">Timeout (h)</label>
              <input className="form-input" type="number" step="0.5" value={pipeline.session_timeout_hours}
                onChange={e => update({ session_timeout_hours: parseFloat(e.target.value) || 4 })}/>
            </div>
          </div>

          {/* Models */}
          <div className="section-label" style={{ marginTop:16 }}>Models</div>
          {pipeline.models.map((m, i) => (
            <div key={i} style={{ display:'flex', gap:6, marginBottom:6 }}>
              <select className="form-input" value={m.model} style={{ flex:1 }}
                onChange={e => {
                  const models = [...pipeline.models]
                  models[i] = { ...m, model: e.target.value }
                  update({ models })
                }}>
                {DEFAULT_MODELS.map(dm => <option key={dm} value={dm}>{dm}</option>)}
              </select>
              <input className="form-input" type="number" value={m.priority} style={{ width:50 }}
                onChange={e => {
                  const models = [...pipeline.models]
                  models[i] = { ...m, priority: parseInt(e.target.value) || 1 }
                  update({ models })
                }}/>
              <button className="btn btn-ghost" style={{ padding:4 }}
                onClick={() => update({ models: pipeline.models.filter((_, j) => j !== i) })}>
                <Trash2 size={12}/>
              </button>
            </div>
          ))}
          <button className="btn btn-ghost" style={{ fontSize:11, padding:'4px 8px' }}
            onClick={() => update({ models: [...pipeline.models, { model: DEFAULT_MODELS[0], priority: pipeline.models.length + 1 }] })}>
            <Plus size={10}/> Add model
          </button>

          {/* Findings board (advanced) */}
          <div style={{ marginTop:20 }}>
            <button className="btn btn-ghost" style={{ fontSize:11, padding:'4px 0' }}
              onClick={() => setShowAdvanced(!showAdvanced)}>
              {showAdvanced ? <ChevronDown size={12}/> : <ChevronRight size={12}/>}
              Advanced
            </button>
            {showAdvanced && (
              <div style={{ marginTop:12 }}>
                <div className="section-label">Findings Board (output)</div>
                {pipeline.findings ? (
                  <div className="card" style={{ padding:12, marginBottom:8 }}>
                    <div style={{ display:'flex', gap:8, marginBottom:8 }}>
                      <div style={{ flex:2 }}>
                        <label className="form-label">Owner</label>
                        <input className="form-input" value={pipeline.findings.project_owner}
                          onChange={e => update({ findings: { ...pipeline.findings!, project_owner: e.target.value } })}/>
                      </div>
                      <div style={{ flex:1 }}>
                        <label className="form-label">#</label>
                        <input className="form-input" type="number" value={pipeline.findings.project_number}
                          onChange={e => update({ findings: { ...pipeline.findings!, project_number: parseInt(e.target.value) || 0 } })}/>
                      </div>
                    </div>
                    <div>
                      <label className="form-label">Initial Status</label>
                      <input className="form-input" value={pipeline.findings.initial_status}
                        onChange={e => update({ findings: { ...pipeline.findings!, initial_status: e.target.value } })}/>
                    </div>
                    <button className="btn btn-danger" style={{ fontSize:11, marginTop:8, padding:'4px 10px' }}
                      onClick={() => update({ findings: null })}>
                      Remove output board
                    </button>
                  </div>
                ) : (
                  <button className="btn btn-secondary" style={{ fontSize:12, width:'100%' }}
                    onClick={() => update({ findings: { project_owner: pipeline.project_owner, project_number: 0, initial_status: 'Analysis' } })}>
                    <Plus size={12}/> Add output board
                  </button>
                )}

                <div className="section-label" style={{ marginTop:16 }}>Allowed Repos</div>
                <textarea className="form-input" rows={3}
                  value={pipeline.allowed_repos.join('\n')}
                  onChange={e => update({ allowed_repos: e.target.value.split('\n').filter(Boolean) })}
                  placeholder="owner/repo (one per line)"/>
              </div>
            )}
          </div>
        </div>

        {/* Right: stage inspector */}
        <div style={{
          flex:1, overflow:'auto',
          borderLeft:'1px solid var(--border)', paddingLeft:16,
        }}>
          {sel ? (
            <div>
              <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:20 }}>
                <h2 style={{ fontSize:16, fontWeight:600, flex:1 }}>
                  Stage: {sel.column || '(unnamed)'}
                </h2>
                <div style={{ display:'flex', gap:4 }}>
                  <button
                    className={`btn ${sel.actor === 'ai' ? 'btn-primary' : 'btn-ghost'}`}
                    style={{ padding:'4px 10px', fontSize:11 }}
                    onClick={() => updateStage(selectedStage, { actor: 'ai' })}>
                    <Bot size={12}/> AI
                  </button>
                  <button
                    className={`btn ${sel.actor === 'human' ? 'btn-primary' : 'btn-ghost'}`}
                    style={{ padding:'4px 10px', fontSize:11 }}
                    onClick={() => updateStage(selectedStage, { actor: 'human' })}>
                    <User size={12}/> Human
                  </button>
                </div>
              </div>

              <div style={{ marginBottom:16 }}>
                <label className="form-label">Column Name</label>
                <input className="form-input" value={sel.column}
                  onChange={e => updateStage(selectedStage, { column: e.target.value })}
                  placeholder="Board column name"/>
              </div>

              {sel.actor === 'ai' && (
                <>
                  <div style={{ marginBottom:16 }}>
                    <label className="form-label">Prompt</label>
                    <textarea className="form-input" rows={16} value={sel.prompt}
                      onChange={e => updateStage(selectedStage, { prompt: e.target.value })}
                      placeholder="# Stage Prompt&#10;&#10;Write the agent instructions here…"
                      style={{ fontFamily:'ui-monospace, monospace', fontSize:12, lineHeight:1.5 }}/>
                    <div style={{ display:'flex', alignItems:'center', gap:8, marginTop:6 }}>
                      <label className="btn btn-ghost" style={{ fontSize:11, padding:'4px 8px', cursor:'pointer' }}>
                        <Upload size={10}/> Load from file
                        <input type="file" accept=".md,.txt" hidden onChange={e => {
                          const file = e.target.files?.[0]
                          if (file) file.text().then(text => updateStage(selectedStage, { prompt: text }))
                        }}/>
                      </label>
                    </div>
                  </div>

                  <div style={{ marginBottom:16 }}>
                    <label className="form-label">Template Variables</label>
                    <div style={{ display:'flex', flexWrap:'wrap', gap:4 }}>
                      {TEMPLATE_VARS.map(v => (
                        <code key={v} style={{
                          fontSize:10, padding:'2px 6px', borderRadius:4,
                          background:'var(--surface2)', border:'1px solid var(--border)',
                          cursor:'pointer', color:'var(--cyan)',
                        }} onClick={() => {
                          // Insert at cursor or append
                          updateStage(selectedStage, { prompt: sel.prompt + ' ' + v })
                        }}>{v}</code>
                      ))}
                      {Object.keys(sel.env).map(k => (
                        <code key={k} style={{
                          fontSize:10, padding:'2px 6px', borderRadius:4,
                          background:'color-mix(in srgb, var(--purple) 12%, transparent)',
                          border:'1px solid color-mix(in srgb, var(--purple) 25%, transparent)',
                          color:'var(--purple)',
                        }}>${'{' + k + '}'}</code>
                      ))}
                    </div>
                  </div>

                  {/* Transitions (advanced) */}
                  <div className="divider"/>
                  <div className="section-label">Transitions</div>
                  <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8, marginBottom:16 }}>
                    <div>
                      <label className="form-label">On Success</label>
                      <select className="form-input" value={sel.on_success}
                        onChange={e => updateStage(selectedStage, { on_success: e.target.value })}>
                        <option value="">(next stage)</option>
                        {pipeline.stages.map((s, i) => i !== selectedStage && (
                          <option key={s.column} value={s.column}>{s.column}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="form-label">On Failure</label>
                      <select className="form-input" value={sel.on_failure}
                        onChange={e => updateStage(selectedStage, { on_failure: e.target.value })}>
                        <option value="">(stay)</option>
                        {pipeline.stages.map((s, i) => i !== selectedStage && (
                          <option key={s.column} value={s.column}>{s.column}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="form-label">On Timeout</label>
                      <select className="form-input" value={sel.on_timeout}
                        onChange={e => updateStage(selectedStage, { on_timeout: e.target.value })}>
                        <option value="">(stay + label)</option>
                        {pipeline.stages.map((s, i) => i !== selectedStage && (
                          <option key={s.column} value={s.column}>{s.column}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Stage env vars */}
                  <div className="section-label">Stage Environment</div>
                  {Object.entries(sel.env).map(([k, v]) => (
                    <div key={k} style={{ display:'flex', gap:6, marginBottom:6 }}>
                      <input className="form-input" value={k} style={{ flex:1 }} placeholder="KEY"
                        onChange={e => {
                          const env = { ...sel.env }
                          const val = env[k]
                          delete env[k]
                          env[e.target.value] = val
                          updateStage(selectedStage, { env })
                        }}/>
                      <input className="form-input" value={v} style={{ flex:2 }} placeholder="value"
                        onChange={e => updateStage(selectedStage, { env: { ...sel.env, [k]: e.target.value } })}/>
                      <button className="btn btn-ghost" style={{ padding:4 }}
                        onClick={() => {
                          const env = { ...sel.env }
                          delete env[k]
                          updateStage(selectedStage, { env })
                        }}>
                        <Trash2 size={12}/>
                      </button>
                    </div>
                  ))}
                  <button className="btn btn-ghost" style={{ fontSize:11, padding:'4px 8px' }}
                    onClick={() => updateStage(selectedStage, { env: { ...sel.env, '': '' } })}>
                    <Plus size={10}/> Add variable
                  </button>
                </>
              )}

              {sel.actor === 'human' && (
                <div className="card" style={{ background:'var(--surface2)', textAlign:'center', padding:'32px 16px' }}>
                  <User size={32} color="var(--muted)"/>
                  <div style={{ color:'var(--muted)', fontSize:13, marginTop:12 }}>
                    Human gate — the pipeline pauses here until a person moves the issue to the next column.
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'100%', color:'var(--muted)' }}>
              <Bot size={32} style={{ marginBottom:12 }}/>
              <div style={{ fontSize:13 }}>Select a stage to edit its prompt and settings</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
