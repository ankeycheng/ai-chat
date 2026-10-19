/** Global conversation-list sync over /api/events (spec v1.2 §5.10). */
import { useEffect, useReducer, useRef, useState } from 'react'
import { api, globalEventsUrl } from '../api/client'
import type { Session } from '../api/types'
import { initialSessions, sessionsReducer } from '../lib/sessionsReducer'
import { usePageActive } from './usePageActive'

export function useGlobalSync() {
  const [state, dispatch] = useReducer(sessionsReducer, initialSessions)
  const [online, setOnline] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const pageActive = usePageActive()

  useEffect(() => {
    if (!pageActive) {
      setOnline(false)
      return
    }
    let disposed = false
    const refetch = async () => {
      try {
        const r = await api.listSessions(undefined, 200)
        if (!disposed) dispatch({ type: 'loaded', items: r.items })
      } catch {
        /* retried on next ready */
      }
    }
    const es = new EventSource(globalEventsUrl())
    esRef.current = es
    es.onopen = () => setOnline(true)
    es.onerror = () => setOnline(false)
    // `ready` arrives on every (re)connect: the list is (re)loaded after the live cursor is fixed,
    // so no change can fall between the snapshot and the subscription.
    es.addEventListener('ready', () => void refetch())
    es.addEventListener('resync', () => void refetch())
    const upsert = (e: Event) => {
      const data = JSON.parse((e as MessageEvent).data) as { session: Session }
      dispatch({ type: 'upsert', session: data.session })
    }
    es.addEventListener('session.created', upsert)
    es.addEventListener('session.updated', upsert)
    es.addEventListener('session.deleted', (e) => {
      const d = JSON.parse((e as MessageEvent).data) as { id: string; version: number }
      dispatch({ type: 'deleted', id: d.id, version: d.version })
    })
    return () => {
      disposed = true
      es.close()
    }
  }, [pageActive])

  return { state, dispatch, online }
}
