import { useEffect, useMemo, useState } from 'react'
import { Check, Loader2, PanelLeftClose, Pencil, Search, SquarePen, Trash2, X } from 'lucide-react'
import { api } from '../api/client'
import type { Session } from '../api/types'
import { groupByDate } from '../lib/sessionsReducer'

interface Props {
  sessions: Session[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onClose: () => void
  onError: (msg: string) => void
  online: boolean
}

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  )
}

function Item({ s, active, q, onSelect, onError }: { s: Session; active: boolean; q: string; onSelect: () => void; onError: (m: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(s.title)
  const save = async () => {
    setEditing(false)
    const t = title.trim()
    if (!t || t === s.title) return setTitle(s.title)
    try {
      await api.renameSession(s.id, t)
    } catch (e) {
      onError((e as Error).message)
      setTitle(s.title)
    }
  }
  const remove = async () => {
    if (!window.confirm(`删除对话「${s.title}」？`)) return
    try {
      await api.deleteSession(s.id)
    } catch (e) {
      onError((e as Error).message)
    }
  }
  if (editing) {
    return (
      <div className="side-item editing">
        <input
          autoFocus
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void save()
            if (e.key === 'Escape') {
              setEditing(false)
              setTitle(s.title)
            }
          }}
          onBlur={() => void save()}
        />
        <button className="icon-btn sm" onMouseDown={(e) => e.preventDefault()} onClick={() => void save()}>
          <Check size={14} />
        </button>
      </div>
    )
  }
  return (
    <div className={`side-item ${active ? 'active' : ''}`} onClick={onSelect} role="button" tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onSelect()}>
      <div className="side-item-text">
        <div className="side-title">
          {s.status === 'generating' && <Loader2 size={12} className="spin gen-dot" />}
          <Highlight text={s.title} q={q} />
        </div>
        {s.match?.field === 'message' && (
          <div className="side-snippet">
            <Highlight text={s.match.snippet} q={q} />
          </div>
        )}
      </div>
      <div className="side-actions" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn sm" title="重命名" onClick={() => setEditing(true)}>
          <Pencil size={14} />
        </button>
        <button className="icon-btn sm" title="删除" onClick={() => void remove()}>
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  )
}

export function Sidebar(p: Props) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Session[] | null>(null)
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const term = q.trim()
    if (!term) {
      setResults(null)
      return
    }
    setSearching(true)
    const t = window.setTimeout(async () => {
      try {
        const r = await api.listSessions(term.slice(0, 100), 50)
        setResults(r.items)
      } catch {
        setResults([])
      } finally {
        setSearching(false)
      }
    }, 250)
    return () => window.clearTimeout(t)
  }, [q])

  // keep search results fresh when titles change via sync
  const liveResults = useMemo(() => {
    if (!results) return null
    const byId = new Map(p.sessions.map((s) => [s.id, s]))
    return results.filter((r) => byId.has(r.id)).map((r) => ({ ...byId.get(r.id)!, match: r.match }))
  }, [results, p.sessions])

  const groups = useMemo(() => groupByDate(p.sessions), [p.sessions])

  return (
    <aside className="sidebar">
      <div className="side-top">
        <button className="icon-btn" onClick={p.onClose} title="收起侧边栏" aria-label="收起侧边栏">
          <PanelLeftClose size={20} />
        </button>
        <div className="brand">AI Chat</div>
        <button className="icon-btn" onClick={p.onNew} title="新对话" aria-label="新对话">
          <SquarePen size={19} />
        </button>
      </div>
      <button className="new-chat" onClick={p.onNew}>
        <SquarePen size={16} /> 新对话
      </button>
      <div className="side-search">
        <Search size={15} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索对话（标题与内容）" aria-label="搜索对话" />
        {searching ? <Loader2 size={14} className="spin" /> : q && (
          <button className="icon-btn xs" onClick={() => setQ('')} aria-label="清除">
            <X size={14} />
          </button>
        )}
      </div>
      <nav className="side-list">
        {liveResults ? (
          liveResults.length ? (
            <div className="side-group">
              <div className="side-group-label">搜索结果</div>
              {liveResults.map((s) => (
                <Item key={s.id} s={s} q={q.trim()} active={s.id === p.activeId} onSelect={() => p.onSelect(s.id)} onError={p.onError} />
              ))}
            </div>
          ) : (
            <div className="side-empty">没有匹配的对话</div>
          )
        ) : groups.length ? (
          groups.map((g) => (
            <div className="side-group" key={g.label}>
              <div className="side-group-label">{g.label}</div>
              {g.items.map((s) => (
                <Item key={s.id} s={s} q="" active={s.id === p.activeId} onSelect={() => p.onSelect(s.id)} onError={p.onError} />
              ))}
            </div>
          ))
        ) : (
          <div className="side-empty">还没有对话</div>
        )}
      </nav>
      <div className="side-foot">
        <span className={`dot ${p.online ? 'ok' : ''}`} /> {p.online ? '多窗口同步已连接' : '同步连接中…'}
      </div>
    </aside>
  )
}
