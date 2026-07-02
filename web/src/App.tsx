import { useEffect, useRef, useState } from 'react'
import { HashRouter, Routes, Route, NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Plug, Columns3, Activity, Settings, Sun, Moon, Menu, X, Workflow, Bot, Wrench, Globe } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Connectors from './pages/Connectors'
import Boards from './pages/Boards'
import Pipelines from './pages/Pipelines'
import PipelineEditor from './pages/PipelineEditor'
import PipelineRuns from './pages/PipelineRuns'
import PipelineLogs from './pages/PipelineLogs'
import Agents from './pages/Agents'
import Skills from './pages/Skills'
import SessionsPage from './pages/Sessions'
import SettingsPage from './pages/Settings'
import Environment from './pages/Environment'
import Wizard from './pages/Wizard'
import AuthCallback from './pages/AuthCallback'

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
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
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

  // Close sidebar on click outside
  useEffect(() => {
    if (!sidebarOpen) return
    const handler = (e: MouseEvent) => {
      if (sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        setSidebarOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [sidebarOpen])

  const closeSidebar = () => setSidebarOpen(false)

  if (needsSetup === null) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh' }}>
        <div className="spinner" style={{ width:32, height:32 }} />
      </div>
    )
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh', overflow:'hidden' }}>
      {/* Header */}
      <header style={{
        height: 56, flexShrink: 0,
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center',
        padding: '0 16px', gap: 12,
        zIndex: 100,
      }}>
        <button
          className="btn btn-ghost"
          onClick={() => setSidebarOpen(o => !o)}
          title="Menu"
          style={{ padding:'6px 8px' }}
        >
          <Menu size={18}/>
        </button>
        <span style={{ fontSize: 18, fontWeight: 700, flex: 1 }} className="brand-glow">🧪 Gru's Lab</span>
        <button className="btn btn-ghost" onClick={toggle} title="Toggle theme" style={{ padding:'6px 8px' }}>
          {theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}
        </button>
        <NavLink to="/settings" title="Settings" style={{ color:'var(--muted)', display:'flex', alignItems:'center', padding:'6px 4px' }}>
          <Settings size={18}/>
        </NavLink>
      </header>

      <div style={{ flex: 1, position:'relative', overflow:'hidden' }}>
        {/* Backdrop */}
        {sidebarOpen && (
          <div
            onClick={closeSidebar}
            style={{
              position:'absolute', inset:0,
              background:'rgba(0,0,0,0.45)',
              zIndex: 49,
              backdropFilter: 'blur(2px)',
            }}
          />
        )}

        {/* Slide-out sidebar */}
        <aside ref={sidebarRef} style={{
          position: 'absolute', top: 0, left: 0, bottom: 0,
          width: 220, zIndex: 50,
          background: 'var(--surface)', borderRight: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column', padding: '0 8px',
          transform: sidebarOpen ? 'translateX(0)' : 'translateX(-100%)',
          transition: 'transform 0.22s cubic-bezier(0.4,0,0.2,1)',
          boxShadow: sidebarOpen ? '4px 0 24px rgba(0,0,0,0.3)' : 'none',
        }}>
          {/* Sidebar header */}
          <div style={{ padding: '14px 8px 12px', borderBottom: '1px solid var(--border)', marginBottom: 8, display:'flex', alignItems:'center', justifyContent:'space-between' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color:'var(--muted)' }}>Navigation</span>
            <button className="btn btn-ghost" onClick={closeSidebar} style={{ padding:'2px 4px' }}>
              <X size={16}/>
            </button>
          </div>
          {/* Nav */}
          <nav style={{ flex: 1, display:'flex', flexDirection:'column', gap:2 }}>
            <NavLink to="/"         onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')} end><LayoutDashboard size={16}/>Dashboard</NavLink>
            <NavLink to="/connectors"  onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Plug size={16}/>Connectors</NavLink>
            <NavLink to="/boards"     onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Columns3 size={16}/>Boards</NavLink>
            <NavLink to="/pipelines" onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Workflow size={16}/>Pipelines</NavLink>
            <NavLink to="/agents"    onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Bot size={16}/>Agents</NavLink>
            <NavLink to="/skills"    onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Wrench size={16}/>Skills</NavLink>
            <NavLink to="/sessions"  onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Activity size={16}/>Sessions</NavLink>
            <NavLink to="/environment" onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Globe size={16}/>Environment</NavLink>
          </nav>
          {/* Bottom */}
          <div style={{ padding: '12px 8px', borderTop: '1px solid var(--border)' }}>
            <NavLink to="/settings" onClick={closeSidebar} className={({isActive}) => 'nav-item' + (isActive ? ' active' : '')}><Settings size={16}/>Settings</NavLink>
          </div>
        </aside>

        {/* Page content */}
        <main style={{ height:'100%', overflow:'auto', padding:24, maxWidth:1200, margin:'0 auto', width:'100%', boxSizing:'border-box' }}>
          <Routes>
            <Route path="/"         element={<Dashboard />} />
            <Route path="/connectors"  element={<Connectors />} />
            <Route path="/boards"        element={<Boards />} />
            <Route path="/pipelines"     element={<Pipelines />} />
            <Route path="/pipelines/:id" element={<PipelineEditor />} />
            <Route path="/pipelines/:id/runs" element={<PipelineRuns />} />
            <Route path="/pipelines/:id/logs" element={<PipelineLogs />} />
            <Route path="/agents"        element={<Agents />} />
            <Route path="/skills"        element={<Skills />} />
            <Route path="/sessions"      element={<SessionsPage />} />
            <Route path="/environment"   element={<Environment />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/wizard"   element={<Wizard onComplete={() => { setNeedsSetup(false); navigate('/') }} />} />
            <Route path="/auth-callback" element={<AuthCallback />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <HashRouter>
      <AppShell />
    </HashRouter>
  )
}
