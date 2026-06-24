import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Plug, Columns3, Activity, Settings, Sun, Moon } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Plugins from './pages/Plugins'
import Boards from './pages/Boards'
import SessionsPage from './pages/Sessions'
import SettingsPage from './pages/Settings'
import Wizard from './pages/Wizard'

function useTheme() {
  const [theme, setTheme] = useState<'dark'|'light'>(() =>
    (localStorage.getItem('gru-theme') as 'dark'|'light') || 'dark'
  )
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('gru-theme', theme)
  }, [theme])
  return { theme, toggle: () => setTheme(t => t === 'dark' ? 'light' : 'dark') }
}

function AppShell() {
  const { theme, toggle } = useTheme()
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    fetch('/api/wizard/status')
      .then(r => r.json())
      .then(d => {
        setNeedsSetup(d.needs_setup)
        if (d.needs_setup) navigate('/wizard', { replace: true })
      })
      .catch(() => setNeedsSetup(false))
  }, [])

  if (needsSetup === null) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh' }}>
        <div className="spinner" style={{ width:32, height:32 }} />
      </div>
    )
  }

  return (
    <div style={{ display:'flex', height:'100vh', overflow:'hidden' }}>
      {/* Sidebar */}
      <aside className="sidebar" style={{
        width: 220, flexShrink: 0,
        background: 'var(--surface)', borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', padding: '0 8px',
      }}>
        {/* Brand */}
        <div style={{ padding: '20px 8px 16px', borderBottom: '1px solid var(--border)', marginBottom: 8 }}>
          <span style={{ fontSize: 20, fontWeight: 700 }} className="brand-glow">🧪 Gru's Lab</span>
        </div>
        {/* Nav */}
        <nav style={{ flex: 1, display:'flex', flexDirection:'column', gap:2 }}>
          <NavLink to="/"        className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')} end><LayoutDashboard size={16}/>Dashboard</NavLink>
          <NavLink to="/plugins" className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Plug size={16}/>Plugins</NavLink>
          <NavLink to="/boards"  className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Columns3 size={16}/>Boards</NavLink>
          <NavLink to="/sessions"className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Activity size={16}/>Sessions</NavLink>
        </nav>
        {/* Bottom */}
        <div style={{ padding: '12px 8px', borderTop: '1px solid var(--border)' }}>
          <NavLink to="/settings" className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Settings size={16}/>Settings</NavLink>
        </div>
      </aside>

      {/* Main */}
      <div style={{ flex: 1, display:'flex', flexDirection:'column', overflow:'hidden' }}>
        {/* Header */}
        <header style={{
          height: 56, flexShrink: 0,
          background: 'var(--surface)', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
          padding: '0 24px', gap: 12,
        }}>
          <button className="btn btn-ghost" onClick={toggle} title="Toggle theme" style={{ padding:'4px 8px' }}>
            {theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}
          </button>
          <NavLink to="/settings" title="Settings" style={{ color:'var(--muted)', display:'flex', alignItems:'center' }}>
            <Settings size={18}/>
          </NavLink>
        </header>

        {/* Page content */}
        <main style={{ flex:1, overflow:'auto', padding:24, maxWidth:1200, margin:'0 auto', width:'100%' }}>
          <Routes>
            <Route path="/"         element={<Dashboard />} />
            <Route path="/plugins"  element={<Plugins />} />
            <Route path="/boards"   element={<Boards />} />
            <Route path="/sessions" element={<SessionsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/wizard"   element={<Wizard onComplete={() => { setNeedsSetup(false); navigate('/') }} />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  )
}
