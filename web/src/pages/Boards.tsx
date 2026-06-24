import { useEffect, useState } from 'react'

export default function Boards() {
  const [boards, setBoards] = useState<any[]>([])

  useEffect(() => {
    fetch('/api/boards').then(r => r.json()).then(setBoards).catch(() => {})
  }, [])

  return (
    <div>
      <h1 style={{ fontSize:22, fontWeight:700, marginBottom:24 }}>Boards</h1>
      {boards.length === 0 ? (
        <div className="card" style={{ color:'var(--muted)', textAlign:'center', padding:32 }}>
          No boards configured. Connect a GitHub or Obsidian plugin to see boards here.
        </div>
      ) : (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(300px,1fr))', gap:16 }}>
          {boards.map((b: any) => (
            <div key={b.id} className="card card-interactive">
              <div style={{ fontWeight:600, marginBottom:4 }}>{b.name}</div>
              <div style={{ fontSize:11, color:'var(--muted)' }}>{b.type} · {b.plugin}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
