import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import { ChatView } from './components/ChatView'
import { Sidebar } from './components/Sidebar'
import { useGlobalSync } from './hooks/useGlobalSync'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useTheme } from './hooks/useTheme'
import { sortedSessions } from './lib/sessionsReducer'

const ACTIVE_KEY = 'activeSessionId'

export default function App() {
  const { state: sessionsState, dispatch: sessionsDispatch, online } = useGlobalSync()
  const sessions = useMemo(() => sortedSessions(sessionsState), [sessionsState])
  const isMobile = useMediaQuery('(max-width: 767px)')
  const [activeId, setActiveId] = useState<string | null>(() => localStorage.getItem(ACTIVE_KEY))
  const [sidebarPref, setSidebarPref] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const { pref: theme, cycle: cycleTheme } = useTheme()
  const sidebarOpen = isMobile ? drawerOpen : sidebarPref

  useEffect(() => {
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
    else localStorage.removeItem(ACTIVE_KEY)
  }, [activeId])

  // conversation deleted (here or in another window) → leave it
  useEffect(() => {
    if (activeId && sessionsState.tombstones[activeId] !== undefined) setActiveId(null)
    if (activeId && sessionsState.loaded && !sessionsState.byId[activeId] && !sessionsState.tombstones[activeId]) {
      // stale id from localStorage
      api.getSession(activeId).catch(() => setActiveId(null))
    }
  }, [activeId, sessionsState])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  // visual viewport height → CSS var (iOS Safari ignores interactive-widget)
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const apply = () => document.documentElement.style.setProperty('--vvh', `${vv.height}px`)
    apply()
    vv.addEventListener('resize', apply)
    return () => vv.removeEventListener('resize', apply)
  }, [])

  const select = useCallback(
    (id: string) => {
      setActiveId(id)
      if (isMobile) setDrawerOpen(false)
    },
    [isMobile],
  )
  const newChat = useCallback(() => {
    setActiveId(null)
    if (isMobile) setDrawerOpen(false)
  }, [isMobile])

  const ensureSession = useCallback(async () => {
    const s = await api.createSession()
    sessionsDispatch({ type: 'upsert', session: s })
    setActiveId(s.id)
    return s.id
  }, [sessionsDispatch])

  return (
    <div className={`app ${sidebarOpen ? 'with-sidebar' : ''} ${isMobile ? 'mobile' : ''}`}>
      {isMobile && drawerOpen && <div className="scrim" onClick={() => setDrawerOpen(false)} />}
      <div className={`sidebar-wrap ${sidebarOpen ? 'open' : ''}`}>
        <Sidebar
          sessions={sessions}
          activeId={activeId}
          onSelect={select}
          onNew={newChat}
          onClose={() => (isMobile ? setDrawerOpen(false) : setSidebarPref(false))}
          onError={setToast}
          online={online}
        />
      </div>
      <ChatView
        sessionId={activeId}
        session={activeId ? sessionsState.byId[activeId] : undefined}
        sidebarOpen={sidebarOpen}
        onOpenSidebar={() => (isMobile ? setDrawerOpen(true) : setSidebarPref(true))}
        onNew={newChat}
        ensureSession={ensureSession}
        theme={theme}
        onCycleTheme={cycleTheme}
        onError={setToast}
      />
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}
