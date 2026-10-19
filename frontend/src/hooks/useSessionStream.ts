/**
 * Per-conversation loading/recovery flow (FR-E08, ADR-017):
 *   1. GET active-run (first, so a run finishing meanwhile cannot be missed)
 *   2. GET latest history window → merge
 *   3. active run → temp message, subscribe after start_event_id; else after last_event_id
 *   4. EventSource auto-reconnect continues with Last-Event-ID; `resync` → reload window.
 *
 * On `done`, the SSE payload includes the full assistant message — no history refetch.
 * On `error`, keep/promote temp with error status (no full refetch).
 */
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api, streamUrl } from '../api/client'
import { chatReducer, initialChatState, oldestSequenceNo, type ChatEvent } from '../lib/chatReducer'
import { usePageActive } from './usePageActive'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'interrupted'

const BUSINESS_EVENTS = ['start', 'delta', 'sources', 'notice', 'done', 'error'] as const
const INTERRUPT_AFTER_MS = 60_000
/** Initial / resync window size for GET /messages?limit= */
export const HISTORY_WINDOW = 50

export function useSessionStream(sessionId: string | null) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [status, setStatus] = useState<ConnectionStatus>('idle')
  const [streamNotice, setStreamNotice] = useState<string | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const timerRef = useRef<number | null>(null)
  const genRef = useRef(0)
  const pageActive = usePageActive()

  const close = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
    if (timerRef.current) window.clearTimeout(timerRef.current)
    timerRef.current = null
  }, [])

  const reloadLatestWindow = useCallback(async (sid: string) => {
    try {
      const page = await api.messages(sid, { limit: HISTORY_WINDOW })
      dispatch({ type: 'history', messages: page.items, hasMoreBefore: page.has_more_before })
    } catch {
      /* transient; next event or reconnect will retry */
    }
  }, [])

  const load = useCallback(
    async (sid: string) => {
      const gen = ++genRef.current
      close()
      setStatus('connecting')
      setStreamNotice(null)
      let after: string | null
      try {
        const run = await api.activeRun(sid)
        const page = await api.messages(sid, { limit: HISTORY_WINDOW })
        if (gen !== genRef.current) return
        dispatch({ type: 'history', messages: page.items, hasMoreBefore: page.has_more_before })
        if (run.active && run.request_id) {
          dispatch({ type: 'activeRun', requestId: run.request_id, thinking: run.thinking ?? undefined })
          after = run.start_event_id ?? run.last_event_id ?? null
        } else {
          after = run.last_event_id ?? null
        }
      } catch {
        if (gen !== genRef.current) return
        setStatus('interrupted')
        return
      }
      const es = new EventSource(streamUrl(sid, after))
      esRef.current = es
      es.onopen = () => {
        if (gen !== genRef.current) return
        setStatus('open')
        if (timerRef.current) window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      es.onerror = () => {
        if (gen !== genRef.current) return
        if (es.readyState === EventSource.CLOSED) {
          setStatus('interrupted')
          return
        }
        setStatus('reconnecting')
        if (!timerRef.current) {
          timerRef.current = window.setTimeout(() => {
            if (gen === genRef.current && es.readyState !== EventSource.OPEN) setStatus('interrupted')
          }, INTERRUPT_AFTER_MS)
        }
      }
      for (const name of BUSINESS_EVENTS) {
        es.addEventListener(name, (e) => {
          if (gen !== genRef.current) return
          const me = e as MessageEvent
          if (!me.lastEventId) return
          const event = { type: name, id: me.lastEventId, data: JSON.parse(me.data) } as ChatEvent
          dispatch({ type: 'event', event })
          // done: promote via event.data.message (no history refetch).
          // error: reducer already sets error status on temp — no refetch.
          // Fallback only if a legacy done arrives without message.
          if (name === 'done') {
            const data = event.data as { message?: unknown; request_id?: string }
            if (!data.message) {
              void (async () => {
                try {
                  const page = await api.messages(sid, { limit: HISTORY_WINDOW })
                  if (gen !== genRef.current) return
                  dispatch({ type: 'history', messages: page.items, hasMoreBefore: page.has_more_before })
                } catch {
                  /* ignore */
                }
              })()
            }
          }
        })
      }
      es.addEventListener('resync', (e) => {
        if (gen !== genRef.current) return
        const data = JSON.parse((e as MessageEvent).data || '{}')
        if (data.reason === 'STREAM_MISSING') {
          setStreamNotice('实时内容已过期或丢失，正在等待任务结束后从历史记录恢复')
        }
        void reloadLatestWindow(sid)
      })
    },
    [close, reloadLatestWindow],
  )

  const loadOlder = useCallback(async () => {
    if (!sessionId || loadingOlder || !state.hasMoreBefore) return
    const before = oldestSequenceNo(state)
    if (before == null) return
    setLoadingOlder(true)
    try {
      const page = await api.messages(sessionId, { before_sequence_no: before, limit: HISTORY_WINDOW })
      dispatch({ type: 'history', messages: page.items, hasMoreBefore: page.has_more_before })
    } catch {
      /* transient */
    } finally {
      setLoadingOlder(false)
    }
  }, [sessionId, loadingOlder, state])

  useEffect(() => {
    dispatch({ type: 'reset', sessionId })
  }, [sessionId])

  useEffect(() => {
    if (!sessionId || !pageActive) {
      close()
      setStatus('idle')
      return
    }
    void load(sessionId)
    const gens = genRef
    return () => {
      gens.current++
      close() // unsubscribing does NOT cancel the backend run
    }
  }, [sessionId, pageActive, load, close])

  const reconnect = useCallback(() => {
    if (sessionId) void load(sessionId)
  }, [sessionId, load])

  return { state, dispatch, status, reconnect, streamNotice, loadOlder, loadingOlder }
}
