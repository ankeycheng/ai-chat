import { memo, useState } from 'react'
import { AlertTriangle, Brain, Check, ChevronDown, Copy, Globe, Info } from 'lucide-react'
import type { UIMessage } from '../lib/chatReducer'
import { Markdown } from './Markdown'

function ReasoningBlock({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false) // collapsed by default (FR-T04)
  return (
    <div className={`reasoning ${open ? 'open' : ''}`}>
      <button className="reasoning-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Brain size={15} />
        <span>{streaming ? '思考中…' : '已深度思考'}</span>
        <ChevronDown size={15} className="chev" />
      </button>
      {open && <div className="reasoning-body">{text}</div>}
    </div>
  )
}

function Sources({ sources }: { sources: UIMessage['sources'] }) {
  if (!sources.length) return null
  return (
    <div className="sources">
      <div className="sources-title">
        <Globe size={14} /> 搜索来源 · {sources.length}
      </div>
      <div className="sources-list">
        {sources.map((s) => (
          <a key={s.id} className="source-chip" href={s.url} target="_blank" rel="noreferrer noopener" title={s.title}>
            <span className="source-id">{s.id}</span>
            {s.site_icon ? <img src={s.site_icon} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
            <span className="source-name">{s.site_name || new URL(s.url).hostname}</span>
            <span className="source-title">{s.title}</span>
          </a>
        ))}
      </div>
    </div>
  )
}

export const MessageItem = memo(function MessageItem({ m }: { m: UIMessage }) {
  const [copied, setCopied] = useState(false)
  if (m.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className={`bubble ${m.status === 'error' ? 'bubble-error' : ''}`}>{m.content}</div>
        {m.error && (
          <div className="inline-error">
            <AlertTriangle size={14} /> {m.error.message}
          </div>
        )}
      </div>
    )
  }
  const streaming = m.status === 'streaming'
  const waiting = streaming && !m.content && !m.reasoning_content
  return (
    <div className="msg msg-assistant">
      <div className="avatar" aria-hidden>
        <svg viewBox="0 0 32 32" width="18" height="18">
          <path d="M9 11h14M9 16h10M9 21h7" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
        </svg>
      </div>
      <div className="msg-body">
        {m.notices.map((n, i) => (
          <div key={i} className="notice">
            <Info size={14} /> {n.message}
          </div>
        ))}
        <Sources sources={m.sources} />
        {m.reasoning_content && <ReasoningBlock text={m.reasoning_content} streaming={streaming && !m.content} />}
        {waiting && (
          <div className="typing">
            <span />
            <span />
            <span />
          </div>
        )}
        {m.content && <Markdown text={m.content + (streaming ? ' ▍' : '')} sources={m.sources} />}
        {m.finish_reason === 'length' && <div className="notice">回答达到长度上限，已截断。</div>}
        {m.finish_reason === 'content_filter' && <div className="notice">部分内容因安全策略被过滤。</div>}
        {m.error && (
          <div className={`error-bar ${m.status === 'interrupted' ? 'warn' : ''}`}>
            <AlertTriangle size={15} />
            <span>{m.status === 'interrupted' ? '生成中断：' : '生成失败：'}{m.error.message}</span>
          </div>
        )}
        {!streaming && m.content && (
          <div className="msg-actions">
            <button
              className="icon-btn sm"
              title="复制"
              onClick={() => {
                void navigator.clipboard?.writeText(m.content)
                setCopied(true)
                setTimeout(() => setCopied(false), 1500)
              }}
            >
              {copied ? <Check size={15} /> : <Copy size={15} />}
            </button>
          </div>
        )}
      </div>
    </div>
  )
})
