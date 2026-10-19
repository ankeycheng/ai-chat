/** Conversation list state synced via the global SSE channel (spec v1.2 §5.10). */
import type { Session } from '../api/types'

export interface SessionsState {
  byId: Record<string, Session>
  tombstones: Record<string, number> // deleted id → version
  loaded: boolean
}

export type SessionsAction =
  | { type: 'loaded'; items: Session[] }
  | { type: 'upsert'; session: Session }
  | { type: 'deleted'; id: string; version: number }

export const initialSessions: SessionsState = { byId: {}, tombstones: {}, loaded: false }

export function sessionsReducer(state: SessionsState, action: SessionsAction): SessionsState {
  switch (action.type) {
    case 'loaded': {
      const byId: Record<string, Session> = {}
      for (const s of action.items) {
        const cur = state.byId[s.id]
        byId[s.id] = cur && cur.version > s.version ? cur : s
      }
      // keep entries newer than the snapshot that arrived via events while loading
      for (const [id, s] of Object.entries(state.byId)) {
        if (!byId[id] && state.loaded === false) byId[id] = s
      }
      for (const id of Object.keys(state.tombstones)) delete byId[id]
      return { ...state, byId, loaded: true }
    }
    case 'upsert': {
      const s = action.session
      const cur = state.byId[s.id]
      const dead = state.tombstones[s.id]
      if (dead !== undefined && dead >= s.version) return state
      if (cur && cur.version >= s.version) return state // stale or duplicate event
      return { ...state, byId: { ...state.byId, [s.id]: { ...s, match: undefined } } }
    }
    case 'deleted': {
      const byId = { ...state.byId }
      delete byId[action.id]
      return { ...state, byId, tombstones: { ...state.tombstones, [action.id]: action.version } }
    }
  }
}

export function sortedSessions(state: SessionsState): Session[] {
  return Object.values(state.byId).sort((a, b) =>
    a.updated_at === b.updated_at ? (a.id < b.id ? 1 : -1) : a.updated_at < b.updated_at ? 1 : -1,
  )
}

export function groupByDate(list: Session[], now = new Date()): { label: string; items: Session[] }[] {
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 86400000
  const groups: { label: string; test: (t: number) => boolean }[] = [
    { label: '今天', test: (t) => t >= startOfToday },
    { label: '昨天', test: (t) => t >= startOfToday - day },
    { label: '7 天内', test: (t) => t >= startOfToday - 7 * day },
    { label: '30 天内', test: (t) => t >= startOfToday - 30 * day },
    { label: '更早', test: () => true },
  ]
  const out = groups.map((g) => ({ label: g.label, items: [] as Session[] }))
  for (const s of list) {
    const t = new Date(s.updated_at).getTime()
    const idx = groups.findIndex((g) => g.test(t))
    out[idx].items.push(s)
  }
  return out.filter((g) => g.items.length)
}
