import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Atom, Globe, Loader2 } from 'lucide-react'

interface Props {
  disabled: boolean
  busy: boolean
  thinking: boolean
  webSearch: boolean
  onToggleThinking: () => void
  onToggleSearch: () => void
  onSend: (text: string) => void
  autoFocus?: boolean
  maxChars: number
}

export function Composer(p: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  const composing = useRef(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }, [text])

  useEffect(() => {
    if (p.autoFocus && window.matchMedia('(pointer: fine)').matches) ref.current?.focus()
  }, [p.autoFocus])

  const canSend = text.trim().length > 0 && !p.busy && !p.disabled && text.length <= p.maxChars
  const submit = () => {
    if (!canSend) return
    p.onSend(text.trim())
    setText('')
  }
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter newline; ignore Enter while an IME composition is active
    if (e.key === 'Enter' && !e.shiftKey && !composing.current && !e.nativeEvent.isComposing) {
      const touch = window.matchMedia('(pointer: coarse)').matches
      if (touch) return // on phones Enter inserts a newline; use the send button
      e.preventDefault()
      submit()
    }
  }
  return (
    <div className="composer">
      <textarea
        ref={ref}
        rows={1}
        value={text}
        placeholder="给 AI 发送消息"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        onCompositionStart={() => (composing.current = true)}
        onCompositionEnd={() => (composing.current = false)}
        enterKeyHint="send"
        aria-label="消息输入框"
      />
      <div className="composer-row">
        <div className="toggles">
          <button
            className={`pill ${p.thinking ? 'on' : ''}`}
            onClick={p.onToggleThinking}
            aria-pressed={p.thinking}
            title="深度思考"
          >
            <Atom size={16} /> 深度思考
          </button>
          <button
            className={`pill ${p.webSearch ? 'on' : ''}`}
            onClick={p.onToggleSearch}
            aria-pressed={p.webSearch}
            title="联网搜索"
          >
            <Globe size={16} /> 联网搜索
          </button>
        </div>
        <div className="composer-right">
          {text.length > p.maxChars * 0.9 && (
            <span className={`counter ${text.length > p.maxChars ? 'over' : ''}`}>
              {text.length}/{p.maxChars}
            </span>
          )}
          <button className="send" onClick={submit} disabled={!canSend} aria-label="发送">
            {p.busy ? <Loader2 size={18} className="spin" /> : <ArrowUp size={18} />}
          </button>
        </div>
      </div>
    </div>
  )
}
