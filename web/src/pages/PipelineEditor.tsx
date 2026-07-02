import { useEffect, useLayoutEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Save, Plus, Trash2, Bot, User, ChevronDown, ChevronRight,
  Upload, Download, RefreshCw, ArrowUp, ArrowDown, X,
  FileUp, Clipboard, Eye, Pencil, Wrench, Play, Pause, AlertTriangle,
} from 'lucide-react'

interface Stage {
  column: string
  actor: string
  agent_id: string
  task_prompt: string
  prompt: string
  on_success: string
  on_failure: string
  on_timeout: string
  env: Record<string, string>
}

interface AgentInfo {
  id: string
  name: string
  description: string
  model: string
  tools: string[]
  skills: string[]
  is_orchestrator: boolean
  lint_errors: string[]
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
  orchestrator_agent_id: string
}

interface PipelineSummary {
  id: string
  name: string
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
    agent_id: s.agent_id || '',
    task_prompt: s.task_prompt || '',
    prompt: s.prompt || '',
    on_success: s.on_success || '',
    on_failure: s.on_failure || '',
    on_timeout: s.on_timeout || '',
    env: s.env || (s.env_json ? JSON.parse(s.env_json) : {}),
  }
}

/** Escape a YAML string value. indentSpaces = spaces before the key line. */
function yamlStr(s: string, indentSpaces = 0): string {
  if (s == null) return '""'
  if (!s) return '""'
  if (s.includes('\n')) {
    const contentIndent = ' '.repeat(indentSpaces + 2)
    return '|\n' + s.split('\n').map(l => contentIndent + l).join('\n')
  }
  if (/[:#{}[\],&*?|>!'"%@`]/.test(s) || s.trim() !== s) return JSON.stringify(s)
  return s
}

/** Export a pipeline to YAML text */
function exportYaml(p: PipelineData): string {
  const lines: string[] = []
  lines.push(`name: ${yamlStr(p.name)}`)
  if (p.plugin_id) lines.push(`plugin_id: ${yamlStr(p.plugin_id)}`)
  if (p.project_owner) lines.push(`project_owner: ${yamlStr(p.project_owner)}`)
  if (p.project_number) lines.push(`project_number: ${p.project_number}`)
  lines.push(`poll_interval: ${p.poll_interval}`)
  lines.push(`max_issues: ${p.max_issues}`)
  lines.push(`max_retries: ${p.max_retries}`)
  lines.push(`session_timeout_hours: ${p.session_timeout_hours}`)
  if (p.models.length > 0) {
    lines.push('models:')
    for (const m of p.models) {
      lines.push(`  - model: ${yamlStr(m.model)}`)
      lines.push(`    priority: ${m.priority}`)
    }
  }
  if (p.allowed_repos.length > 0) {
    lines.push('allowed_repos:')
    for (const r of p.allowed_repos) lines.push(`  - ${yamlStr(r)}`)
  }
  if (p.stages.length > 0) {
    lines.push('stages:')
    for (const s of p.stages) {
      lines.push(`  - column: ${yamlStr(s.column, 4)}`)
      lines.push(`    actor: ${s.actor}`)
      if (s.agent_id) lines.push(`    agent_id: ${yamlStr(s.agent_id, 4)}`)
      if (s.task_prompt) lines.push(`    task_prompt: ${yamlStr(s.task_prompt, 4)}`)
      if (s.prompt) lines.push(`    prompt: ${yamlStr(s.prompt, 4)}`)
      if (s.on_success) lines.push(`    on_success: ${yamlStr(s.on_success, 4)}`)
      if (s.on_failure) lines.push(`    on_failure: ${yamlStr(s.on_failure, 4)}`)
      if (s.on_timeout) lines.push(`    on_timeout: ${yamlStr(s.on_timeout, 4)}`)
      if (Object.keys(s.env).length > 0) {
        lines.push('    env:')
        for (const [k, v] of Object.entries(s.env)) {
          lines.push(`      ${k}: ${yamlStr(v, 6)}`)
        }
      }
    }
  }
  if (p.findings) {
    lines.push('findings_project:')
    lines.push(`  project_owner: ${yamlStr(p.findings.project_owner)}`)
    lines.push(`  project_number: ${p.findings.project_number}`)
    lines.push(`  initial_status: ${yamlStr(p.findings.initial_status)}`)
  }
  return lines.join('\n') + '\n'
}

// ── Tool colour palette — deterministic hash, no mutable global state ─────────
const PALETTE = [
  '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444',
  '#06b6d4', '#84cc16', '#f97316', '#ec4899', '#6366f1',
]
function toolColor(tool: string): string {
  const hash = tool.split('').reduce((acc, c) => (acc * 31 + c.charCodeAt(0)) | 0, 0)
  return PALETTE[Math.abs(hash) % PALETTE.length]
}

// ── Blueprint Graph (SVG-based dynamic routing diagram) ──────────────────────

const CARD_W = 152
const H_GAP = 44     // gap between cards for arrows

interface GraphEdge { from: number; to: number; type: 'success' | 'failure' }

function BlueprintGraph({ pipeline, agentMap, onEditStage, onEdit }: {
  pipeline: PipelineData
  agentMap: Record<string, AgentInfo>
  onEditStage: (i: number) => void
  onEdit: () => void
}) {
  const cardRefs = useRef<(HTMLDivElement | null)[]>([])
  const containerRef = useRef<HTMLDivElement>(null)
  const [arrows, setArrows] = useState<{ x1:number; y1:number; x2:number; y2:number; type:'success'|'failure'; arc:number }[]>([])
  const [svgSize, setSvgSize] = useState({ w: 0, h: 0 })

  const stages = pipeline.stages

  // Build edge list from on_success / on_failure
  const edges: GraphEdge[] = []
  stages.forEach((s, i) => {
    const colIdx = (col: string) => stages.findIndex(st => st.column === col)
    if (s.on_success) {
      const to = colIdx(s.on_success)
      if (to !== -1) edges.push({ from: i, to, type: 'success' })
    }
    if (s.on_failure) {
      const to = colIdx(s.on_failure)
      if (to !== -1) edges.push({ from: i, to, type: 'failure' })
    }
  })

  // Measure card positions and compute SVG arrow paths
  const measureArrows = useCallback(() => {
    const container = containerRef.current
    if (!container) return
    const containerRect = container.getBoundingClientRect()

    const rects = cardRefs.current.map(el => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: r.left - containerRect.left, y: r.top - containerRect.top, w: r.width, h: r.height }
    })

    const maxRight = Math.max(...rects.filter(Boolean).map(r => r!.x + r!.w), 0)
    const maxBottom = Math.max(...rects.filter(Boolean).map(r => r!.y + r!.h), 0)

    const computed = edges.map(e => {
      const fr = rects[e.from]
      const tr = rects[e.to]
      if (!fr || !tr) return null

      const isForward = e.to > e.from
      const isAdjacent = Math.abs(e.to - e.from) === 1

      const x1 = fr.x + fr.w
      const y1 = fr.y + fr.h / 2
      const x2 = tr.x
      const y2 = tr.y + tr.h / 2

      const skip = Math.abs(e.to - e.from) - 1
      const arc = isAdjacent ? 0 : (isForward ? -(skip * 30 + 20) : (skip * 30 + 20))

      return { x1, y1, x2, y2, type: e.type, arc }
    }).filter(Boolean) as typeof arrows

    setSvgSize({ w: maxRight + 20, h: maxBottom + Math.abs(Math.min(0, ...computed.map(a => a.arc))) + 40 })
    setArrows(computed)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edges.map(e => `${e.from}-${e.to}-${e.type}`).join(',')])

  // Use layout effect + ResizeObserver so arrows re-measure when card heights
  // change (agent data loads, lint errors appear, content changes).
  useLayoutEffect(() => {
    measureArrows()
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(measureArrows)
    ro.observe(container)
    cardRefs.current.forEach(el => { if (el) ro.observe(el) })
    return () => ro.disconnect()
  }, [measureArrows])

  const svgPaddingTop = Math.abs(Math.min(0, ...arrows.map(a => a.arc), 0)) + 10

  if (stages.length === 0) {
    return (
      <div style={{ padding:'32px 24px', color:'var(--muted)', fontSize:13, textAlign:'center',
        border:'1px dashed var(--border)', borderRadius:8 }}>
        No stages. <button className="btn btn-ghost" style={{ fontSize:12 }} onClick={onEdit}>Switch to Edit</button> to add.
      </div>
    )
  }

  return (
    <div style={{ overflowX:'auto', paddingBottom:8 }}>
      <div ref={containerRef} style={{ position:'relative', display:'inline-block', minWidth:'max-content' }}>
        {/* Cards row */}
        <div style={{ display:'flex', alignItems:'flex-start', gap:0, paddingTop: svgPaddingTop }}>
          {stages.map((stage, i) => {
            const ag = stage.agent_id ? agentMap[stage.agent_id] : null
            const isHuman = stage.actor === 'human'
            const tools = ag ? ag.tools : []
            const hasLintErrors = ag && ag.lint_errors.length > 0

            return (
              <div key={i} style={{ display:'flex', alignItems:'center' }}>
                <div
                  ref={el => { cardRefs.current[i] = el }}
                  onClick={() => onEditStage(i)}
                  title={hasLintErrors ? `⚠ Lint errors: ${ag!.lint_errors.join('; ')}` : 'Click to edit this stage'}
                  style={{
                    width: CARD_W, borderRadius:8,
                    border: hasLintErrors
                      ? '1px solid color-mix(in srgb, var(--yellow) 60%, transparent)'
                      : '1px solid var(--border)',
                    background: hasLintErrors
                      ? 'color-mix(in srgb, var(--yellow) 5%, var(--surface))'
                      : isHuman
                        ? 'color-mix(in srgb, var(--muted) 8%, var(--surface))'
                        : 'var(--surface)',
                    cursor:'pointer', overflow:'hidden', flexShrink:0,
                    display:'flex', flexDirection:'column',
                    transition:'border-color 0.15s, box-shadow 0.15s',
                    opacity: hasLintErrors ? 0.8 : 1,
                  }}
                  onMouseEnter={e => {
                    ;(e.currentTarget as HTMLDivElement).style.borderColor = hasLintErrors ? 'var(--yellow)' : 'var(--accent)'
                    ;(e.currentTarget as HTMLDivElement).style.boxShadow = `0 0 0 3px color-mix(in srgb, ${hasLintErrors ? 'var(--yellow)' : 'var(--accent)'} 15%, transparent)`
                  }}
                  onMouseLeave={e => {
                    ;(e.currentTarget as HTMLDivElement).style.borderColor = hasLintErrors ? 'color-mix(in srgb, var(--yellow) 60%, transparent)' : 'var(--border)'
                    ;(e.currentTarget as HTMLDivElement).style.boxShadow = 'none'
                  }}
                >
                  {/* Header */}
                  <div style={{
                    padding:'7px 10px', display:'flex', alignItems:'center', gap:6,
                    borderBottom:'1px solid var(--border)', flexShrink:0,
                    background: hasLintErrors
                      ? 'color-mix(in srgb, var(--yellow) 10%, transparent)'
                      : isHuman
                        ? 'color-mix(in srgb, var(--muted) 6%, transparent)'
                        : 'color-mix(in srgb, var(--accent) 8%, transparent)',
                  }}>
                    <div style={{
                      width:20, height:20, borderRadius:4, display:'flex', alignItems:'center', justifyContent:'center',
                      background: hasLintErrors
                        ? 'color-mix(in srgb, var(--yellow) 25%, transparent)'
                        : isHuman ? 'var(--surface2)' : 'color-mix(in srgb, var(--accent) 20%, transparent)',
                      color: hasLintErrors ? 'var(--yellow)' : isHuman ? 'var(--muted)' : 'var(--accent)',
                      flexShrink:0,
                    }}>
                      {hasLintErrors ? <AlertTriangle size={11}/> : isHuman ? <User size={11}/> : <Bot size={11}/>}
                    </div>
                    <span style={{ fontSize:12, fontWeight:700, flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                      {stage.column || '(unnamed)'}
                    </span>
                    <span style={{ fontSize:9, color:'var(--muted)' }}>#{i + 1}</span>
                  </div>

                  {/* Agent */}
                  <div style={{ padding:'8px 10px 7px', borderBottom:'1px solid var(--border)' }}>
                    {hasLintErrors ? (
                      <>
                        <div style={{ fontSize:11, fontWeight:600, color:'var(--text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', marginBottom:3 }}>
                          {ag!.name || ag!.id}
                        </div>
                        <span style={{ fontSize:10, color:'var(--yellow)', fontWeight:600 }}>⚠ {ag!.lint_errors.length} dep error{ag!.lint_errors.length === 1 ? '' : 's'}</span>
                      </>
                    ) : isHuman ? (
                      <div style={{ color:'var(--muted)', fontSize:11, paddingTop:6 }}>Human gate</div>
                    ) : ag ? (
                      <>
                        <div style={{ fontSize:11, fontWeight:600, color:'var(--text)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', marginBottom:3 }}>
                          {ag.name || ag.id}
                        </div>
                        {ag.model && (
                          <span style={{ fontSize:9, padding:'1px 5px', borderRadius:3, background:'color-mix(in srgb, var(--purple) 15%, transparent)', color:'var(--purple)', fontWeight:600 }}>
                            {ag.model}
                          </span>
                        )}
                      </>
                    ) : (
                      <div style={{ fontSize:10, color:'var(--red)', paddingTop:6 }}>No agent assigned</div>
                    )}
                  </div>

                  {/* Tools */}
                  <div style={{ padding:'8px 10px', borderBottom:'1px solid var(--border)' }}>
                    <div style={{ display:'flex', flexWrap:'wrap', gap:3 }}>
                      {tools.length > 0 ? tools.map(t => (
                        <span key={t} style={{
                          fontSize:9, padding:'2px 6px', borderRadius:3, fontWeight:600,
                          background:`color-mix(in srgb, ${toolColor(t)} 18%, transparent)`,
                          color: toolColor(t), border:`1px solid color-mix(in srgb, ${toolColor(t)} 30%, transparent)`,
                        }}>{t}</span>
                      )) : <span style={{ fontSize:9, color:'var(--muted)' }}>—</span>}
                    </div>
                  </div>

                  {/* Skills */}
                  <div style={{ padding:'8px 10px' }}>
                    <div style={{ display:'flex', flexWrap:'wrap', gap:3 }}>
                      {ag && ag.skills.length > 0 ? ag.skills.map((sk: string) => {
                        const label = sk.replace(/^skills\/[\w-]+\//, '').replace('.sh', '')
                        return (
                          <span key={sk} title={sk} style={{
                            fontSize:9, padding:'2px 6px', borderRadius:3, fontWeight:600,
                            background:'color-mix(in srgb, var(--green) 12%, transparent)',
                            color:'var(--green)', border:'1px solid color-mix(in srgb, var(--green) 25%, transparent)',
                          }}>{label}</span>
                        )
                      }) : <span style={{ fontSize:9, color:'var(--muted)' }}>—</span>}
                    </div>
                  </div>
                </div>

                {/* Spacer between cards (arrows drawn via SVG) */}
                {i < stages.length - 1 && (
                  <div style={{ width: H_GAP, flexShrink:0 }} />
                )}
              </div>
            )
          })}
        </div>

        {/* SVG arrow overlay */}
        {arrows.length > 0 && (
          <svg
            style={{ position:'absolute', top:0, left:0, pointerEvents:'none', overflow:'visible' }}
            width={svgSize.w} height={svgSize.h + svgPaddingTop}
          >
            <defs>
              <marker id="arrow-success" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill="var(--green)" />
              </marker>
              <marker id="arrow-failure" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill="var(--red)" />
              </marker>
              <marker id="arrow-muted" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill="var(--border)" />
              </marker>
            </defs>
            {arrows.map((a, idx) => {
              const color = a.type === 'success' ? 'var(--green)' : 'var(--red)'
              const markerId = a.type === 'success' ? 'arrow-success' : 'arrow-failure'
              const label = a.type === 'success' ? '✓' : '✗'
              const adjY = a.y1 + svgPaddingTop
              const adjY2 = a.y2 + svgPaddingTop

              if (a.arc === 0) {
                // Straight horizontal arrow for adjacent stages
                const mx = (a.x1 + a.x2) / 2
                return (
                  <g key={idx}>
                    <line
                      x1={a.x1} y1={adjY} x2={a.x2 - 7} y2={adjY2}
                      stroke={color} strokeWidth="1.5" markerEnd={`url(#${markerId})`}
                      strokeDasharray={a.type === 'failure' ? '4 3' : undefined}
                    />
                    <text x={mx} y={adjY - 5} textAnchor="middle"
                      fontSize="9" fontWeight="700" fill={color}>{label}</text>
                  </g>
                )
              } else {
                // Curved arc for non-adjacent connections
                const cy = adjY + a.arc  // control point y
                const cy2 = adjY2 + a.arc
                const mx = (a.x1 + a.x2) / 2
                const pathD = `M ${a.x1} ${adjY} C ${a.x1 + 20} ${cy}, ${a.x2 - 20} ${cy2}, ${a.x2} ${adjY2}`
                // label midpoint on arc
                const lx = mx
                const ly = (adjY + adjY2) / 2 + a.arc * 0.8
                return (
                  <g key={idx}>
                    <path d={pathD} fill="none" stroke={color} strokeWidth="1.5"
                      markerEnd={`url(#${markerId})`}
                      strokeDasharray={a.type === 'failure' ? '4 3' : undefined}
                    />
                    <text x={lx} y={ly} textAnchor="middle"
                      fontSize="9" fontWeight="700" fill={color}>{label}</text>
                  </g>
                )
              }
            })}
          </svg>
        )}
      </div>
    </div>
  )
}

// ── Pipeline Blueprint view ───────────────────────────────────────────────────

interface BlueprintProps {
  pipeline: PipelineData
  agents: AgentInfo[]
  running: boolean
  onEditStage: (idx: number) => void   // switches to edit mode at that stage
  onEdit: () => void                   // switches to full edit mode
}

function PipelineBlueprint({ pipeline, agents, running, onEditStage, onEdit }: BlueprintProps) {
  const agentMap = Object.fromEntries(agents.map(a => [a.id, a]))
  const aiStages = pipeline.stages.filter(s => s.actor === 'ai')

  // Collect all unique tools + skills across all stages
  const allTools = Array.from(new Set(
    pipeline.stages.flatMap(s => {
      const ag = s.agent_id ? agentMap[s.agent_id] : null
      return ag ? ag.tools : []
    })
  ))
  const allSkills = Array.from(new Set(
    pipeline.stages.flatMap(s => {
      const ag = s.agent_id ? agentMap[s.agent_id] : null
      return ag ? ag.skills : []
    })
  ))

  // tool/skill → list of stage indices that have it
  const toolStageMap: Record<string, number[]> = {}
  const skillStageMap: Record<string, number[]> = {}
  pipeline.stages.forEach((s, i) => {
    const ag = s.agent_id ? agentMap[s.agent_id] : null
    if (!ag) return
    ag.tools.forEach(t => {
      if (!toolStageMap[t]) toolStageMap[t] = []
      toolStageMap[t].push(i)
    })
    ag.skills.forEach(sk => {
      if (!skillStageMap[sk]) skillStageMap[sk] = []
      skillStageMap[sk].push(i)
    })
  })

  const sharedTools = allTools
    .filter(t => toolStageMap[t]?.length > 1)
    .sort((a, b) => (toolStageMap[b]?.length || 0) - (toolStageMap[a]?.length || 0))

  const sharedSkills = allSkills
    .filter(sk => skillStageMap[sk]?.length > 1)
    .sort((a, b) => (skillStageMap[b]?.length || 0) - (skillStageMap[a]?.length || 0))

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:24 }}>

      {/* ── Orchestrator ── */}
      {(() => {
        const orchAgents = agents.filter(a => a.is_orchestrator)
        const assigned = pipeline.orchestrator_agent_id ? agentMap[pipeline.orchestrator_agent_id] : null
        return (
          <div>
            <div style={{ fontSize:11, fontWeight:700, color:'var(--muted)', letterSpacing:'0.08em', marginBottom:12, textTransform:'uppercase' }}>
              Orchestrator
            </div>
            <div style={{
              display:'flex', alignItems:'center', gap:12,
              padding:'12px 16px', borderRadius:8, border:'1px solid var(--border)',
              background: assigned ? 'color-mix(in srgb, var(--purple) 6%, var(--surface))' : 'var(--surface2)',
              borderColor: assigned ? 'color-mix(in srgb, var(--purple) 35%, transparent)' : 'var(--border)',
            }}>
              <div style={{
                width:32, height:32, borderRadius:8, display:'flex', alignItems:'center', justifyContent:'center',
                background: assigned ? 'color-mix(in srgb, var(--purple) 18%, transparent)' : 'var(--surface)',
                color: assigned ? 'var(--purple)' : 'var(--muted)', flexShrink:0,
              }}>
                <Bot size={16}/>
              </div>
              {assigned ? (
                <div style={{ flex:1 }}>
                  <div style={{ fontSize:13, fontWeight:700, color:'var(--text)' }}>{assigned.name}</div>
                  <div style={{ fontSize:11, color:'var(--muted)', marginTop:2 }}>{assigned.model || 'no model set'}</div>
                </div>
              ) : (
                <div style={{ flex:1 }}>
                  <div style={{ fontSize:13, fontWeight:600, color:'var(--muted)' }}>No orchestrator assigned</div>
                  <div style={{ fontSize:11, color:'var(--muted)', marginTop:2 }}>
                    {orchAgents.length === 0
                      ? 'Mark an agent as Orchestrator in the Agents page first'
                      : `${orchAgents.length} orchestrator agent${orchAgents.length > 1 ? 's' : ''} available — assign via Edit mode`}
                  </div>
                </div>
              )}
              {assigned && (
                <span style={{ fontSize:10, padding:'2px 8px', borderRadius:4, background:'color-mix(in srgb, var(--purple) 15%, transparent)', color:'var(--purple)', fontWeight:700 }}>
                  ORCHESTRATOR
                </span>
              )}
            </div>
          </div>
        )
      })()}

      {/* ── Stage Flow ── */}
      <div>
        <div style={{ fontSize:11, fontWeight:700, color:'var(--muted)', letterSpacing:'0.08em', marginBottom:12, textTransform:'uppercase' }}>
          Stage Flow
        </div>
        <BlueprintGraph pipeline={pipeline} agentMap={agentMap} onEditStage={onEditStage} onEdit={onEdit} />
      </div>

      {/* ── Bottom: Shared Tools + Stats ── */}
      <div style={{ display:'flex', gap:20 }}>

        {/* Shared Tools */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ fontSize:11, fontWeight:700, color:'var(--muted)', letterSpacing:'0.08em', marginBottom:12, textTransform:'uppercase', display:'flex', alignItems:'center', gap:6 }}>
            <Wrench size={11}/> Shared Tools
          </div>

          {sharedTools.length > 0 ? (
            <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
              {sharedTools.map(tool => {
                const stageIdxs = toolStageMap[tool] || []
                const pct = Math.round((stageIdxs.length / Math.max(aiStages.length, 1)) * 100)
                return (
                  <div key={tool}>
                    <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
                      <span style={{
                        fontSize:11, fontWeight:700, padding:'2px 7px', borderRadius:4,
                        background:`color-mix(in srgb, ${toolColor(tool)} 18%, transparent)`,
                        color: toolColor(tool), border:`1px solid color-mix(in srgb, ${toolColor(tool)} 30%, transparent)`,
                        flexShrink:0,
                      }}>{tool}</span>
                      <span style={{ fontSize:11, color:'var(--muted)', marginLeft:'auto', flexShrink:0 }}>
                        {stageIdxs.length}/{aiStages.length} stages
                      </span>
                    </div>
                    {/* Bar */}
                    <div style={{ height:6, borderRadius:3, background:'var(--surface2)', overflow:'hidden' }}>
                      <div style={{
                        height:'100%', width:`${pct}%`, borderRadius:3,
                        background: toolColor(tool), transition:'width 0.4s',
                      }}/>
                    </div>
                    {/* Which stages */}
                    <div style={{ display:'flex', flexWrap:'wrap', gap:3, marginTop:4 }}>
                      {stageIdxs.map(idx => (
                        <button key={idx} className="btn btn-ghost"
                          onClick={() => onEditStage(idx)}
                          style={{ fontSize:9, padding:'1px 5px', borderRadius:3, border:'1px solid var(--border)', color:'var(--muted)' }}>
                          {pipeline.stages[idx]?.column || `#${idx+1}`}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="card" style={{ padding:14, color:'var(--muted)', fontSize:12, textAlign:'center' }}>
              <Wrench size={20} style={{ marginBottom:8 }}/>
              <div>Tools will appear here once agents share the same tool across stages.</div>
            </div>
          )}
        </div>

        {/* Shared Skills */}
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ fontSize:11, fontWeight:700, color:'var(--muted)', letterSpacing:'0.08em', marginBottom:12, textTransform:'uppercase', display:'flex', alignItems:'center', gap:6 }}>
            🔧 Shared Skills
          </div>

          {sharedSkills.length > 0 ? (
            <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
              {sharedSkills.map(sk => {
                const stageIdxs = skillStageMap[sk] || []
                const pct = Math.round((stageIdxs.length / Math.max(aiStages.length, 1)) * 100)
                const label = sk.replace(/^skills\/[\w-]+\//, '').replace('.sh', '')
                return (
                  <div key={sk}>
                    <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
                      <span title={sk} style={{
                        fontSize:11, fontWeight:700, padding:'2px 7px', borderRadius:4,
                        background:'color-mix(in srgb, var(--green) 12%, transparent)',
                        color:'var(--green)', border:'1px solid color-mix(in srgb, var(--green) 25%, transparent)',
                        flexShrink:0,
                      }}>{label}</span>
                      <span style={{ fontSize:11, color:'var(--muted)', marginLeft:'auto', flexShrink:0 }}>
                        {stageIdxs.length}/{aiStages.length} stages
                      </span>
                    </div>
                    <div style={{ height:6, borderRadius:3, background:'var(--surface2)', overflow:'hidden' }}>
                      <div style={{ height:'100%', width:`${pct}%`, borderRadius:3, background:'var(--green)', transition:'width 0.4s' }}/>
                    </div>
                    <div style={{ display:'flex', flexWrap:'wrap', gap:3, marginTop:4 }}>
                      {stageIdxs.map(idx => (
                        <button key={idx} className="btn btn-ghost"
                          onClick={() => onEditStage(idx)}
                          style={{ fontSize:9, padding:'1px 5px', borderRadius:3, border:'1px solid var(--border)', color:'var(--muted)' }}>
                          {pipeline.stages[idx]?.column || `#${idx+1}`}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="card" style={{ padding:14, color:'var(--muted)', fontSize:12, textAlign:'center' }}>
              <div style={{ fontSize:20, marginBottom:8 }}>🔧</div>
              <div>Skills shared across 2+ stages will appear here.</div>
            </div>
          )}
        </div>

        {/* Pipeline stats summary */}
        <div style={{ width:200, flexShrink:0 }}>
          <div className="card" style={{ padding:12 }}>
            <div style={{ display:'flex', alignItems:'center', gap:10, marginBottom:10 }}>
              <span style={{ fontSize:11, fontWeight:600, color:'var(--muted)', flex:1 }}>Pipeline Stats</span>
              <div style={{ display:'flex', alignItems:'center', gap:6 }}>
                <div className={`dot ${running ? 'dot-green' : 'dot-muted'}`} style={{ width:7, height:7, borderRadius:'50%' }}/>
                <span style={{ fontSize:11, fontWeight:600, color: running ? 'var(--green)' : 'var(--muted)' }}>
                  {running ? 'Running' : 'Paused'}
                </span>
              </div>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              {[
                { label:'Stages', value: pipeline.stages.length },
                { label:'AI stages', value: aiStages.length },
                { label:'Human gates', value: pipeline.stages.length - aiStages.length },
                { label:'Shared tools', value: sharedTools.length },
                { label:'Shared skills', value: sharedSkills.length },
                { label:'Lint errors', value: agents.filter(a => a.lint_errors?.length).length, warn: true },
              ].map(({ label, value, warn }) => (
                <div key={label} style={{ textAlign:'center', padding:'6px 0' }}>
                  <div style={{ fontSize:20, fontWeight:800, color: warn && value > 0 ? 'var(--yellow)' : 'var(--accent)' }}>{value}</div>
                  <div style={{ fontSize:10, color:'var(--muted)' }}>{label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Import Modal ──────────────────────────────────────────────────────────────

function ImportModal({ onImport, onClose }: {
  onImport: (yaml: string, pipelineId?: string, overwrite?: boolean) => Promise<void>
  onClose: () => void
}) {
  const [yamlText, setYamlText] = useState('')
  const [pipelineId, setPipelineId] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) file.text().then(t => { setYamlText(t); setError('') })
  }

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText()
      setYamlText(text)
      setError('')
    } catch { setError('Clipboard permission denied — paste manually below') }
  }

  const handleImport = async () => {
    if (!yamlText.trim()) { setError('Paste or upload YAML content first'); return }
    setImporting(true)
    setError('')
    try {
      await onImport(yamlText, pipelineId || undefined, overwrite)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setImporting(false)
    }
  }

  return (
    <div style={{
      position:'fixed', inset:0, zIndex:1000, display:'flex', alignItems:'center', justifyContent:'center',
      background:'rgba(0,0,0,0.6)', backdropFilter:'blur(4px)',
    }} onClick={onClose}>
      <div style={{
        background:'var(--surface)', border:'1px solid var(--border)', borderRadius:12,
        padding:24, width:580, maxHeight:'85vh', overflow:'auto',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:16 }}>
          <h2 style={{ fontSize:16, fontWeight:600 }}>Import Pipeline from YAML</h2>
          <button className="btn btn-ghost" onClick={onClose} style={{ padding:4 }}><X size={16}/></button>
        </div>

        <div style={{ marginBottom:12 }}>
          <label className="form-label">Pipeline ID <span style={{ color:'var(--muted)', fontWeight:400 }}>(optional — derived from name if blank)</span></label>
          <input className="form-input" value={pipelineId}
            onChange={e => setPipelineId(e.target.value)}
            placeholder="e.g. my-pipeline"/>
        </div>

        <div style={{ display:'flex', gap:8, marginBottom:10 }}>
          <label className="btn btn-secondary" style={{ fontSize:12, cursor:'pointer' }}>
            <FileUp size={12}/> Upload .yml
            <input type="file" accept=".yml,.yaml" hidden onChange={handleFile}/>
          </label>
          <button className="btn btn-secondary" style={{ fontSize:12 }} onClick={handlePaste}>
            <Clipboard size={12}/> Paste from clipboard
          </button>
        </div>

        <div style={{ marginBottom:12 }}>
          <label className="form-label">YAML Config</label>
          <textarea className="form-input" rows={14} value={yamlText}
            onChange={e => { setYamlText(e.target.value); setError('') }}
            placeholder={'name: My Pipeline\nplugin_id: ghe-roommate\nstages:\n  - column: Todo\n    actor: ai\n    prompt: "Process issue #${ISSUE_NUM}"\n  - column: Review\n    actor: human'}
            style={{ fontFamily:'ui-monospace, monospace', fontSize:12, lineHeight:1.5 }}/>
        </div>

        <label style={{ display:'flex', alignItems:'center', gap:8, marginBottom:16, cursor:'pointer', fontSize:13 }}>
          <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)}/>
          Overwrite if pipeline ID already exists
        </label>

        {error && (
          <div style={{
            color:'var(--red)', fontSize:12, marginBottom:12,
            padding:'8px 10px', background:'color-mix(in srgb, var(--red) 10%, transparent)',
            borderRadius:6, border:'1px solid color-mix(in srgb, var(--red) 25%, transparent)',
          }}>{error}</div>
        )}

        <div style={{ display:'flex', justifyContent:'flex-end', gap:8 }}>
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleImport} disabled={importing || !yamlText.trim()}>
            <Upload size={14}/> {importing ? 'Importing…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function PipelineEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const isNew = id === 'new'

  const [pipeline, setPipeline] = useState<PipelineData | null>(null)
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [selectedStage, setSelectedStage] = useState<number>(-1)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [connectors, setConnectors] = useState<any[]>([])
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [fetchingColumns, setFetchingColumns] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [viewMode, setViewMode] = useState(!isNew)
  const [running, setRunning] = useState(false)
  const [toggling, setToggling] = useState(false)

  useEffect(() => {
    fetch('/api/plugins').then(r => r.json()).then(setConnectors).catch(() => {})
    fetch('/api/agents').then(r => r.json()).then(setAgents).catch(() => {})
    fetch('/api/pipelines').then(r => r.json())
      .then((list: any[]) => setPipelines(list.map(p => ({ id: p.id, name: p.name }))))
      .catch(() => {})

    if (isNew) {
      setPipeline({
        id: '', name: '', enabled: true, plugin_id: '', board_type: 'github',
        project_owner: '', project_number: 0, board_path: null,
        stages: [], poll_interval: 300, max_issues: 50, max_retries: 3,
        session_timeout_hours: 4, models: [], allowed_repos: [], findings: null,
        orchestrator_agent_id: '',
      })
    } else {
      fetch(`/api/pipelines/${id}`).then(r => r.json()).then(data => {
        data.stages = (data.stages || []).map(normalizeStage)
        setPipeline(data)
        setRunning(!!data.enabled)
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
      const oldCol = stages[idx].column
      stages[idx] = { ...stages[idx], ...patch }
      // If the column name changed, cascade the rename to all on_success/on_failure/on_timeout refs
      if (patch.column !== undefined && patch.column !== oldCol) {
        const newCol = patch.column
        for (let j = 0; j < stages.length; j++) {
          if (j === idx) continue
          stages[j] = {
            ...stages[j],
            on_success: stages[j].on_success === oldCol ? newCol : stages[j].on_success,
            on_failure: stages[j].on_failure === oldCol ? newCol : stages[j].on_failure,
            on_timeout: stages[j].on_timeout === oldCol ? newCol : stages[j].on_timeout,
          }
        }
      }
      return { ...prev, stages }
    })
    setDirty(true)
  }, [])

  const addStage = () => {
    if (!pipeline) return
    const newStage: Stage = {
      column: '', actor: 'ai', agent_id: '', task_prompt: '',
      prompt: '', on_success: '', on_failure: '', on_timeout: '', env: {},
    }
    update({ stages: [...pipeline.stages, newStage] })
    setSelectedStage(pipeline.stages.length)
  }

  const removeStage = (idx: number) => {
    if (!pipeline) return
    const stage = pipeline.stages[idx]
    if (stage.prompt || stage.task_prompt) {
      if (!window.confirm(`Remove stage "${stage.column || '(unnamed)'}"? Its prompt will be lost.`)) return
    }
    const stages = pipeline.stages.filter((_, i) => i !== idx)
    update({ stages })
    setSelectedStage(prev => {
      if (prev === idx) return Math.min(idx, stages.length - 1)
      if (prev > idx) return prev - 1
      return prev
    })
  }

  const moveStage = (idx: number, direction: -1 | 1) => {
    if (!pipeline) return
    const target = idx + direction
    if (target < 0 || target >= pipeline.stages.length) return
    const stages = [...pipeline.stages]
    ;[stages[idx], stages[target]] = [stages[target], stages[idx]]
    update({ stages })
    setSelectedStage(target)
  }

  const fetchColumns = async () => {
    if (!pipeline?.plugin_id || !pipeline.project_owner || !pipeline.project_number) return
    const hasContent = pipeline.stages.some(s => s.prompt || s.task_prompt)
    if (hasContent && !window.confirm('Fetching board columns will replace all current stages and their prompts. Continue?')) return
    setFetchingColumns(true)
    setFetchError(null)
    try {
      const resp = await fetch(
        `/api/pipelines/board-columns/${pipeline.plugin_id}?owner=${pipeline.project_owner}&number=${pipeline.project_number}`
      )
      if (!resp.ok) throw new Error(await resp.text())
      const { columns } = await resp.json()
      const stages: Stage[] = columns.map((col: string) => ({
        column: col,
        actor: ['Review', 'Done', 'Backlog'].some(h => col.toLowerCase().includes(h.toLowerCase())) ? 'human' : 'ai',
        agent_id: '', task_prompt: '',
        prompt: '', on_success: '', on_failure: '', on_timeout: '', env: {},
      }))
      update({ stages })
      if (stages.length > 0) setSelectedStage(0)
    } catch (e: any) {
      setFetchError('Failed to fetch columns: ' + e.message)
    } finally {
      setFetchingColumns(false)
    }
  }

  const toggleRunning = async () => {
    if (!pipeline || isNew) return
    setToggling(true)
    setSaveError(null)
    try {
      const endpoint = running ? 'stop' : 'start'
      const resp = await fetch(`/api/pipelines/${pipeline.id}/${endpoint}`, { method: 'POST' })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `Failed to ${endpoint} pipeline` }))
        throw new Error(err.detail || `Failed to ${endpoint} pipeline`)
      }
      setRunning(r => !r)
    } catch (e: any) {
      setSaveError(e.message)
    } finally {
      setToggling(false)
    }
  }

  const save = async () => {
    if (!pipeline) return
    setSaving(true)
    setSaveError(null)
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
      // Refresh pipeline list
      fetch('/api/pipelines').then(r => r.json())
        .then((list: any[]) => setPipelines(list.map(p => ({ id: p.id, name: p.name }))))
        .catch(() => {})
      if (isNew) navigate(`/pipelines/${saved.id}`, { replace: true })
    } catch (e: any) {
      setSaveError(e.message)
    } finally {
      setSaving(false)
    }
  }

  // Unsaved-changes guard — browser back/refresh
  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  // Ctrl+S / Cmd+S to save (only in Edit mode)
  const saveRef = useRef(save)
  useEffect(() => { saveRef.current = save })
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && !viewMode) {
        e.preventDefault()
        saveRef.current()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [viewMode])

  const navigateSafe = (path: string) => {
    if (dirty && !window.confirm('You have unsaved changes. Leave without saving?')) return
    navigate(path)
  }

  const handleExport = () => {
    if (!pipeline) return
    const yaml = exportYaml(pipeline)
    const blob = new Blob([yaml], { type: 'text/yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${pipeline.id || 'pipeline'}.yml`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImport = async (yamlText: string, pipelineId?: string, overwrite?: boolean) => {
    // If overwrite requested, delete existing pipeline first
    if (overwrite && pipelineId) {
      await fetch(`/api/pipelines/${pipelineId}`, { method: 'DELETE' })
    }
    const resp = await fetch('/api/pipelines/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config_yaml: yamlText, pipeline_id: pipelineId }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: 'Import failed' }))
      throw new Error(err.detail || 'Import failed')
    }
    const imported = await resp.json()
    setShowImport(false)
    navigate(`/pipelines/${imported.id}`, { replace: true })
  }

  if (!pipeline) return (
    <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)', padding:48 }}>
      <div className="spinner"/> Loading…
    </div>
  )

  const sel = selectedStage >= 0 && selectedStage < pipeline.stages.length
    ? pipeline.stages[selectedStage]
    : null

  const githubConnectors = connectors.filter(p => p.plugin_type === 'github')

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'calc(100vh - 56px - 48px)' }}>
      {showImport && <ImportModal onImport={handleImport} onClose={() => setShowImport(false)}/>}

      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', gap:12,
        padding:'0 0 16px', borderBottom:'1px solid var(--border)', marginBottom:16,
      }}>
        <button className="btn btn-ghost" onClick={() => navigateSafe('/')} style={{ padding:'6px 8px' }}>
          <ArrowLeft size={16}/>
        </button>

        {/* Pipeline selector dropdown */}
        <select
          className="form-input"
          value={pipeline.id}
          onChange={e => {
            const val = e.target.value
            if (val === '__new__') navigateSafe('/pipelines/new')
            else if (val) navigateSafe(`/pipelines/${val}`)
          }}
          style={{ width:200, fontSize:13, fontWeight:600 }}
        >
          {isNew && <option value="">New Pipeline</option>}
          {pipelines.map(p => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
          <option value="__new__">＋ New pipeline…</option>
        </select>

        <div style={{ flex:1 }}/>
        {dirty && <span style={{ fontSize:11, color:'var(--yellow)' }}>unsaved</span>}

        {/* Mode toggle */}
        <div style={{ display:'flex', borderRadius:6, border:'1px solid var(--border)', overflow:'hidden' }}>
          <button
            className={`btn ${viewMode ? 'btn-primary' : 'btn-ghost'}`}
            style={{ borderRadius:0, padding:'5px 10px', fontSize:12, gap:5 }}
            onClick={() => setViewMode(true)}>
            <Eye size={13}/> Blueprint
          </button>
          <button
            className={`btn ${!viewMode ? 'btn-primary' : 'btn-ghost'}`}
            style={{ borderRadius:0, padding:'5px 10px', fontSize:12, gap:5, borderLeft:'1px solid var(--border)' }}
            onClick={() => setViewMode(false)}>
            <Pencil size={13}/> Edit
          </button>
        </div>

        {/* Start / Pause */}
        {!isNew && (
          <button
            className={`btn ${running ? 'btn-secondary' : 'btn-primary'}`}
            style={{ fontSize:12, padding:'5px 12px', gap:5 }}
            onClick={toggleRunning}
            disabled={toggling}>
            {running
              ? <><Pause size={13}/> {toggling ? 'Pausing…' : 'Pause'}</>
              : <><Play size={13}/> {toggling ? 'Starting…' : 'Start'}</>}
          </button>
        )}

        {viewMode ? (
          <button className="btn btn-ghost" onClick={handleExport} style={{ fontSize:12, padding:'6px 10px' }}
            title="Export pipeline as YAML" disabled={isNew}>
            <Download size={14}/> Export
          </button>
        ) : (
          <>
            <button className="btn btn-ghost" onClick={() => setShowImport(true)} style={{ fontSize:12, padding:'6px 10px' }}
              title="Import pipeline from YAML">
              <Upload size={14}/> Import
            </button>
            <button className="btn btn-ghost" onClick={handleExport} style={{ fontSize:12, padding:'6px 10px' }}
              title="Export pipeline as YAML" disabled={isNew}>
              <Download size={14}/> Export
            </button>
            <button className="btn btn-primary" onClick={save} disabled={saving || !pipeline.id || !pipeline.name}>
              <Save size={14}/> {saving ? 'Saving…' : 'Save'}
            </button>
          </>
        )}
      </div>

      {/* Error banner (save / toggle errors) */}
      {saveError && (
        <div style={{
          display:'flex', alignItems:'center', gap:8, padding:'8px 12px',
          background:'color-mix(in srgb, var(--red) 10%, transparent)',
          border:'1px solid color-mix(in srgb, var(--red) 25%, transparent)',
          borderRadius:6, marginBottom:12, fontSize:12, color:'var(--red)',
        }}>
          <span style={{ flex:1 }}>{saveError}</span>
          <button className="btn btn-ghost" onClick={() => setSaveError(null)} style={{ padding:2, color:'var(--red)' }}>
            <X size={12}/>
          </button>
        </div>
      )}

      {/* ── Blueprint / Editor content ── */}
      {viewMode ? (
        <div style={{ flex:1, overflow:'auto' }}>
          <PipelineBlueprint
            pipeline={pipeline}
            agents={agents}
            running={running}
            onEditStage={idx => { setViewMode(false); setSelectedStage(idx) }}
            onEdit={() => setViewMode(false)}
          />
        </div>
      ) : (
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
            <label className="form-label">Connector</label>
            <select className="form-input" value={pipeline.plugin_id}
              onChange={e => update({ plugin_id: e.target.value })}>
              <option value="">Select connector…</option>
              {githubConnectors.map(p => (
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
          <button className="btn btn-secondary" style={{ width:'100%', marginBottom:4, fontSize:12 }}
            onClick={fetchColumns} disabled={fetchingColumns || !pipeline.plugin_id || !pipeline.project_owner || !pipeline.project_number}>
            {fetchingColumns ? <><div className="spinner" style={{ width:12, height:12 }}/> Fetching…</> :
              <><RefreshCw size={12}/> Fetch Board Columns</>}
          </button>
          {fetchError && (
            <div style={{ fontSize:11, color:'var(--red)', marginBottom:12, padding:'4px 6px',
              background:'color-mix(in srgb, var(--red) 8%, transparent)', borderRadius:4 }}>
              {fetchError}
            </div>
          )}

          {/* Stages */}
          <div className="section-label">Stages</div>
          <div style={{ display:'flex', flexDirection:'column', gap:4, marginBottom:12 }}>
            {pipeline.stages.length === 0 ? (
              <div style={{
                padding:'16px 12px', textAlign:'center', color:'var(--muted)', fontSize:12,
                border:'1px dashed var(--border)', borderRadius:6,
              }}>
                No stages yet. Fetch from board or add manually.
              </div>
            ) : pipeline.stages.map((stage, i) => (
              <div key={i}
                className={`card card-interactive ${selectedStage === i ? 'card-active' : ''}`}
                onClick={() => setSelectedStage(i)}
                style={{
                  padding:'8px 12px', display:'flex', alignItems:'center', gap:6, cursor:'pointer',
                }}>
                {/* Reorder arrows */}
                <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
                  <button className="btn btn-ghost" style={{ padding:1, lineHeight:0 }}
                    disabled={i === 0}
                    onClick={e => { e.stopPropagation(); moveStage(i, -1) }}>
                    <ArrowUp size={10}/>
                  </button>
                  <button className="btn btn-ghost" style={{ padding:1, lineHeight:0 }}
                    disabled={i === pipeline.stages.length - 1}
                    onClick={e => { e.stopPropagation(); moveStage(i, 1) }}>
                    <ArrowDown size={10}/>
                  </button>
                </div>
                <div style={{
                  width:20, height:20, borderRadius:4, display:'flex', alignItems:'center', justifyContent:'center',
                  background: stage.actor === 'ai' ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'var(--surface2)',
                  color: stage.actor === 'ai' ? 'var(--accent)' : 'var(--muted)',
                }}>
                  {stage.actor === 'ai' ? <Bot size={12}/> : <User size={12}/>}
                </div>
                <span style={{ flex:1, fontSize:13, fontWeight:500 }}>{stage.column || '(unnamed)'}</span>
                {/* Prompt indicator */}
                {(stage.prompt || stage.task_prompt) && (
                  <span title={`${(stage.prompt || stage.task_prompt).length} chars`} style={{
                    fontSize:9, padding:'1px 4px', borderRadius:3,
                    background:'color-mix(in srgb, var(--accent) 20%, transparent)',
                    color:'var(--accent)', fontWeight:600,
                  }}>P</span>
                )}
                <button className="btn btn-ghost" style={{ padding:2 }}
                  onClick={e => { e.stopPropagation(); removeStage(i) }}>
                  <Trash2 size={12}/>
                </button>
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

          {/* Orchestrator */}
          <div className="section-label" style={{ marginTop:16 }}>Orchestrator Agent</div>
          {(() => {
            const orchAgents = agents.filter(a => a.is_orchestrator)
            return (
              <select className="form-input" style={{ width:'100%', marginBottom:8 }}
                value={pipeline.orchestrator_agent_id || ''}
                onChange={e => update({ orchestrator_agent_id: e.target.value })}>
                <option value="">— None —</option>
                {orchAgents.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
                {orchAgents.length === 0 && (
                  <option disabled value="">No orchestrator agents yet — mark one in the Agents page</option>
                )}
              </select>
            )
          })()}

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
                {!DEFAULT_MODELS.includes(m.model) && m.model && (
                  <option key={m.model} value={m.model}>{m.model}</option>
                )}
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
                  {/* Agent selector — agent library is the only way to configure AI behaviour */}
                  <div style={{ marginBottom:16 }}>
                    <label className="form-label">Agent</label>
                    <select className="form-input"
                      value={sel.agent_id || ''}
                      onChange={e => updateStage(selectedStage, { agent_id: e.target.value })}>
                      <option value="">— Unassigned —</option>
                      {agents.map(a => (
                        <option key={a.id} value={a.id}>{a.name} ({a.id})</option>
                      ))}
                    </select>
                    {sel.agent_id ? (() => {
                      const ag = agents.find(a => a.id === sel.agent_id)
                      if (!ag) return <p style={{ fontSize: 11, color: 'var(--red)', marginTop: 4 }}>Agent not found in library — edit in the Agents page</p>
                      return (
                        <div style={{ marginTop: 6, padding: '8px 10px', borderRadius: 6, background: 'var(--surface2)', border: '1px solid var(--border)', fontSize: 12 }}>
                          <span style={{ color: 'var(--muted)' }}>{ag.description}</span>
                          <div style={{ display: 'flex', gap: 6, marginTop: 4, flexWrap: 'wrap' }}>
                            {ag.model && <span className="badge" style={{ background: 'color-mix(in srgb, var(--purple) 15%, transparent)', color: 'var(--purple)' }}>{ag.model}</span>}
                            {ag.tools.map(t => <span key={t} className="badge">{t}</span>)}
                          </div>
                        </div>
                      )
                    })() : (
                      <p style={{ fontSize: 11, color: 'var(--yellow)', marginTop: 6 }}>
                        ⚠ No agent assigned. Go to <strong>Agents</strong> to create or upload an agent, then assign it here.
                      </p>
                    )}
                  </div>

                  {/* Task prompt — short override instruction, expanded at runtime */}
                  {sel.agent_id && (
                    <div style={{ marginBottom:16 }}>
                      <label className="form-label">Task Prompt <span style={{ fontWeight:400, color:'var(--muted)' }}>(optional override)</span></label>
                      <textarea className="form-input" rows={3}
                        value={sel.task_prompt || ''}
                        onChange={e => updateStage(selectedStage, { task_prompt: e.target.value })}
                        placeholder="Process issue #${ISSUE_NUM} at stage ${ISSUE_STAGE}"
                        style={{ fontFamily:'ui-monospace, monospace', fontSize:12 }}/>
                      <p style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                        Short instruction prepended to the agent prompt. Template variables are expanded. Leave empty to use the agent as-is.
                      </p>
                      <div style={{ display:'flex', flexWrap:'wrap', gap:4, marginTop:6 }}>
                        {TEMPLATE_VARS.map(v => (
                          <code key={v} style={{
                            fontSize:10, padding:'2px 6px', borderRadius:4,
                            background:'var(--surface2)', border:'1px solid var(--border)',
                            cursor:'pointer', color:'var(--cyan)',
                          }} onClick={() => updateStage(selectedStage, { task_prompt: (sel.task_prompt || '') + ' ' + v })}>{v}</code>
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
                  )}

                  {/* Transitions (advanced) */}
                  <div className="divider"/>
                  <div className="section-label">Transitions</div>
                  <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8, marginBottom:16 }}>
                    <div>
                      <label className="form-label">On Success →</label>
                      <select className="form-input" value={sel.on_success}
                        onChange={e => updateStage(selectedStage, { on_success: e.target.value })}>
                        <option value="">(no automatic move)</option>
                        {pipeline.stages.map((s, i) => i !== selectedStage && (
                          <option key={s.column} value={s.column}>{s.column}</option>
                        ))}
                      </select>
                      <p style={{ fontSize:10, color:'var(--muted)', marginTop:3 }}>
                        Stage to move issue to after a successful session.
                      </p>
                    </div>
                    <div>
                      <label className="form-label">On Failure →</label>
                      <select className="form-input" value={sel.on_failure}
                        onChange={e => updateStage(selectedStage, { on_failure: e.target.value })}>
                        <option value="">(stay in current stage)</option>
                        {pipeline.stages.map((s, i) => i !== selectedStage && (
                          <option key={s.column} value={s.column}>{s.column}</option>
                        ))}
                      </select>
                      <p style={{ fontSize:10, color:'var(--muted)', marginTop:3 }}>
                        Stage to move issue to after a failed session (leave blank to retry in-place).
                      </p>
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
      )}
    </div>
  )
}
