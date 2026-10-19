import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, Menu, Moon, Monitor, RefreshCw, SquarePen, Sun, WifiOff } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Session } from '../api/types'
import { isStreaming } from '../lib/chatReducer'
import { useSessionStream, type ConnectionStatus } from '../hooks/useSessionStream'
import type { ThemePref } from '../hooks/useTheme'
import { Composer } from './Composer'
import { MessageItem } from './MessageItem'

interface Props {
  sessionId: string | null
  session: Session | undefined
  sidebarOpen: boolean
  onOpenSidebar: () => void
  onNew: () => void
  ensureSession: () => Promise<string>
  theme: ThemePref
  onCycleTheme: () => void
  onError: (msg: string) => void
}

const STATUS_TEXT: Record<ConnectionStatus, string> = {
  idle: '',
  connecting: '连接中',
  open: '已连接',
  reconnecting: '重连中…',
  interrupted: '连接中断',
}

const uuid = () =>
  typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
        (+c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (+c / 4)))).toString(16),
      )

export function ChatView(p: Props) {
  const { state, dispatch, status, reconnect, streamNotice, loadOlder, loadingOlder } = useSessionStream(p.sessionId)
  const [thinking, setThinking] = useState(() => localStorage.getItem('opt.thinking') === '1')
  const [webSearch, setWebSearch] = useState(() => localStorage.getItem('opt.search') === '1')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)
  const streaming = isStreaming(state) || p.session?.status === 'generating'

  useEffect(() => localStorage.setItem('opt.thinking', thinking ? '1' : '0'), [thinking])
  useEffect(() => localStorage.setItem('opt.search', webSearch ? '1' : '0'), [webSearch])

  const scrollToBottom = useCallback((smooth = false) => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }, [])

  useLayoutEffect(() => {
    if (atBottom) scrollToBottom()
  }, [state.messages, atBottom, scrollToBottom])

  useEffect(() => {
    setAtBottom(true)
  }, [p.sessionId])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80)
  }

  const send = async (text: string) => {
    const requestId = uuid()
    dispatch({ type: 'localSend', requestId, content: text, thinking })
    setAtBottom(true)
    setSending(true)
    try {
      const sid = p.sessionId ?? (await p.ensureSession())
      const body = { content: text, thinking, web_search: webSearch, request_id: requestId }
      // the previous run may still be finalising right after `done`: retry with the SAME request_id
      for (let attempt = 0; ; attempt++) {
        try {
          await api.send(sid, body)
          break
        } catch (e) {
          if (e instanceof ApiError && e.code === 'GENERATION_IN_PROGRESS' && attempt < 5 && !isStreaming(state)) {
            await new Promise((r) => setTimeout(r, 400 * (attempt + 1)))
            continue
          }
          if (e instanceof ApiError && (e.status === 0 || e.status >= 500) && attempt < 2) {
            await new Promise((r) => setTimeout(r, 800))
            continue
          }
          throw e
        }
      }
    } catch (e) {
      const err = e as ApiError
      dispatch({ type: 'sendFailed', requestId, code: err.code ?? 'ERROR', message: err.message })
      p.onError(err.message)
    } finally {
      setSending(false)
    }
  }

  const empty = state.messages.length === 0 && (!p.sessionId || state.historyLoaded)
  const ThemeIcon = p.theme === 'dark' ? Moon : p.theme === 'light' ? Sun : Monitor

  return (
    <main className="chat">
      <header className="chat-head">
        {!p.sidebarOpen && (
          <button className="icon-btn" onClick={p.onOpenSidebar} aria-label="打开侧边栏" title="打开侧边栏">
            <Menu size={20} />
          </button>
        )}
        <div className="chat-title" title={p.session?.title}>
          {p.session?.title ?? 'AI Chat'}
        </div>
        <div className="head-right">
          {p.sessionId && status !== 'open' && status !== 'idle' && (
            <span className={`conn conn-${status}`}>
              {status === 'interrupted' ? <WifiOff size={13} /> : null}
              {STATUS_TEXT[status]}
              {status === 'interrupted' && (
                <button className="link-btn" onClick={reconnect}>
                  <RefreshCw size={12} /> 重连
                </button>
              )}
            </span>
          )}
          <button className="icon-btn" onClick={p.onCycleTheme} title={`主题：${p.theme}`} aria-label="切换主题">
            <ThemeIcon size={18} />
          </button>
          {!p.sidebarOpen && (
            <button className="icon-btn" onClick={p.onNew} aria-label="新对话" title="新对话">
              <SquarePen size={19} />
            </button>
          )}
        </div>
      </header>

      <div className={`chat-scroll ${empty ? 'is-empty' : ''}`} ref={scrollRef} onScroll={onScroll}>
        {empty ? (
          <div className="hero">
            <h1>有什么可以帮忙的？</h1>
          </div>
        ) : (
          <div className="thread">
            {streamNotice && <div className="notice">{streamNotice}</div>}
            {state.hasMoreBefore && (
              <div className="load-older">
                <button className="load-older-btn" disabled={loadingOlder} onClick={() => void loadOlder()}>
                  {loadingOlder ? '加载中…' : '加载更早消息'}
                </button>
              </div>
            )}
            {state.messages.map((m) => (
              <MessageItem key={m.key} m={m} />
            ))}
            <div className="thread-end" />
          </div>
        )}
      </div>

      <div className={`composer-dock ${empty ? 'centered' : ''}`}>
        {!atBottom && !empty && (
          <button className="to-bottom" onClick={() => scrollToBottom(true)} aria-label="滚动到底部">
            <ArrowDown size={18} />
          </button>
        )}
        <Composer
          disabled={false}
          busy={streaming || sending}
          thinking={thinking}
          webSearch={webSearch}
          onToggleThinking={() => setThinking((v) => !v)}
          onToggleSearch={() => setWebSearch((v) => !v)}
          onSend={(t) => void send(t)}
          autoFocus
          maxChars={32000}
        />
        <div className="disclaimer">AI 生成内容可能有误，请核实重要信息。</div>
      </div>
    </main>
  )
}
