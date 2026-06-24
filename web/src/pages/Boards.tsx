import { useEffect, useState } from 'react'
import { RefreshCw, ChevronDown, ChevronRight as ChevronRightIcon } from 'lucide-react'

function BoardCard({ board }: { board: any }) {
  const [expanded, setExpanded] = useState(false)
  const [columns, setColumns] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  const toggle = async () => {
    if (!expanded && columns.length === 0) {
      setLoading(true)
      try {
        const r = await fetch(`/api/boards/${board.id}/columns`)
        const data = await r.json()
        setColumns(Array.isArray(data) ? data : [])
      } catch { setColumns([]) }
      finally { setLoading(false) }
    }
    setExpanded(e => !e)
  }

  const typeEmoji = board.type === 'github' ? '🐙' : board.type === 'obsidian' ? '📝' : '📋'

  return (
    <div className="card" style={{ padding:0, overflow:'hidden' }}>
      {/* Header row */}
      <div
        onClick={toggle}
        style={{ display:'flex', alignItems:'center', gap:10, padding:'14px 16px', cursor:'pointer', userSelect:'none' }}
      >
        <span style={{ fontSize:18 }}>{typeEmoji}</span>
        <div style={{ flex:1 }}>
          <div style={{ fontWeight:600, fontSize:14 }}>{board.name}</div>
          <div style={{ fontSize:11, color:'var(--muted)' }}>{board.type} · {board.plugin_id}</div>
        </div>
        {loading
          ? <div className="spinner" style={{ width:14, height:14 }}/>
          : expanded ? <ChevronDown size={14} color="var(--muted)"/> : <ChevronRightIcon size={14} color="var(--muted)"/>
        }
      </div>

      {/* Columns */}
      {expanded && (
        <div style={{ borderTop:'1px solid var(--border)', padding:'12px 16px' }}>
          {columns.length === 0 ? (
            <div style={{ color:'var(--muted)', fontSize:12, textAlign:'center', padding:'8px 0' }}>No columns found</div>
          ) : (
            <div style={{ display:'flex', gap:10, overflowX:'auto', paddingBottom:4 }}>
              {columns.map((col: any) => (
                <div key={col.name} style={{ minWidth:160, flexShrink:0 }}>
                  <div style={{ fontSize:11, fontWeight:600, color:'var(--muted)', textTransform:'uppercase', letterSpacing:'0.06em', marginBottom:6 }}>
                    {col.name}
                    <span style={{ marginLeft:6, fontWeight:400 }}>({col.card_count ?? col.cards?.length ?? 0})</span>
                  </div>
                  {(col.cards || []).slice(0, 5).map((card: any, i: number) => (
                    <div key={i} style={{
                      background:'var(--surface2)', borderRadius:6, padding:'8px 10px', marginBottom:6,
                      fontSize:12, lineHeight:1.4, border:'1px solid var(--border)',
                    }}>
                      {card.title || card.text || card}
                    </div>
                  ))}
                  {(col.cards?.length || 0) > 5 && (
                    <div style={{ fontSize:11, color:'var(--muted)', textAlign:'center' }}>+{col.cards.length - 5} more</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Boards() {
  const [boards, setBoards] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    fetch('/api/boards').then(r => r.json()).then(d => { setBoards(Array.isArray(d) ? d : []); setLoading(false) })
      .catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:24 }}>
        <h1 style={{ fontSize:22, fontWeight:700 }}>Boards</h1>
        <button className="btn btn-ghost" onClick={load}><RefreshCw size={14}/> Refresh</button>
      </div>

      {loading && boards.length === 0 && (
        <div style={{ display:'flex', gap:12, alignItems:'center', color:'var(--muted)' }}><div className="spinner"/> Loading…</div>
      )}

      {boards.length === 0 && !loading ? (
        <div className="card" style={{ color:'var(--muted)', textAlign:'center', padding:48 }}>
          <div style={{ fontSize:32, marginBottom:12 }}>📋</div>
          No boards configured yet. Connect a GitHub or Obsidian plugin to see boards here.
          <br/><a href="/plugins" style={{ fontSize:13, color:'var(--accent)', marginTop:12, display:'inline-block' }}>Go to Plugins →</a>
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
          {boards.map((b: any) => <BoardCard key={b.id} board={b}/>)}
        </div>
      )}
    </div>
  )
}
