/**
 * Per-conversation message state & merge algorithm (spec §9.4).
 * Pure reducer: history and SSE may overlap; messages are merged by message_id /
 * (request_id, role) and never blindly appended. Temp assistant messages are keyed by
 * request_id and replaced by the persisted message once history contains it, or once
 * `done` carries the full `message` payload.
 */
import type { Message, Source } from '../api/types'
import { isNewer } from './streamId'

export type UIStatus = 'sending' | 'streaming' | 'done' | 'error' | 'interrupted'

export interface UIMessage {
  key: string
  id?: string
  request_id: string
  role: 'user' | 'assistant'
  content: string
  reasoning_content: string
  sources: Source[]
  sequence_no: number | null
  temp: boolean
  status: UIStatus
  thinking?: boolean
  notices: { code: string; message: string }[]
  error?: { code: string; message: string }
  finish_reason?: string | null
  created_at?: string
}

export interface ChatState {
  sessionId: string | null
  messages: UIMessage[]
  activeRequestId: string | null
  lastAppliedId: string | null
  terminal: Record<string, 'done' | 'error'>
  historyLoaded: boolean
  hasMoreBefore: boolean
}

export type ChatEvent =
  | { type: 'start'; id: string; data: { request_id: string; thinking?: boolean; user_message?: Partial<Message> } }
  | { type: 'delta'; id: string; data: { request_id: string; kind: 'reasoning' | 'content'; text: string } }
  | { type: 'sources'; id: string; data: { request_id: string; sources: Source[] } }
  | { type: 'notice'; id: string; data: { request_id: string; code: string; message: string } }
  | {
      type: 'done'
      id: string
      data: {
        request_id: string
        message_id: string
        finish_reason?: string
        message?: Message
      }
    }
  | { type: 'error'; id: string; data: { request_id: string; code: string; message: string; status?: string } }

export type ChatAction =
  | { type: 'reset'; sessionId: string | null }
  | { type: 'history'; messages: Message[]; hasMoreBefore?: boolean }
  | { type: 'activeRun'; requestId: string; thinking?: boolean }
  | { type: 'event'; event: ChatEvent }
  | { type: 'localSend'; requestId: string; content: string; thinking: boolean }
  | { type: 'sendFailed'; requestId: string; code: string; message: string }

export const initialChatState: ChatState = {
  sessionId: null,
  messages: [],
  activeRequestId: null,
  lastAppliedId: null,
  terminal: {},
  historyLoaded: false,
  hasMoreBefore: false,
}

const fromServer = (m: Message): UIMessage => ({
  key: m.id,
  id: m.id,
  request_id: m.request_id,
  role: m.role,
  content: m.content,
  reasoning_content: m.reasoning_content,
  sources: m.sources ?? [],
  sequence_no: m.sequence_no,
  temp: false,
  status: 'done',
  notices: [],
  finish_reason: m.finish_reason,
  created_at: m.created_at,
})

const tempAssistant = (requestId: string, thinking?: boolean): UIMessage => ({
  key: `temp:${requestId}:assistant`,
  request_id: requestId,
  role: 'assistant',
  content: '',
  reasoning_content: '',
  sources: [],
  sequence_no: null,
  temp: true,
  status: 'streaming',
  thinking,
  notices: [],
})

function sortMessages(list: UIMessage[]): UIMessage[] {
  // persisted messages by sequence_no; temp ones keep arrival order after their user message
  const withIndex = list.map((m, i) => ({ m, i }))
  withIndex.sort((a, b) => {
    const sa = a.m.sequence_no ?? Number.MAX_SAFE_INTEGER
    const sb = b.m.sequence_no ?? Number.MAX_SAFE_INTEGER
    if (sa !== sb) return sa - sb
    return a.i - b.i
  })
  return withIndex.map((x) => x.m)
}

const hasPersisted = (list: UIMessage[], rid: string, role: 'user' | 'assistant') =>
  list.some((m) => !m.temp && m.request_id === rid && m.role === role)

function upsertUser(list: UIMessage[], um: Partial<Message> | undefined, rid: string): UIMessage[] {
  if (!um || !um.id) return list
  if (list.some((m) => m.id === um.id)) return list
  const persisted: UIMessage = {
    key: um.id,
    id: um.id,
    request_id: rid,
    role: 'user',
    content: um.content ?? '',
    reasoning_content: '',
    sources: [],
    sequence_no: um.sequence_no ?? null,
    temp: false,
    status: 'done',
    notices: [],
    created_at: um.created_at,
  }
  // replace optimistic local copy
  const filtered = list.filter((m) => !(m.temp && m.role === 'user' && m.request_id === rid))
  return [...filtered, persisted]
}

function ensureTemp(list: UIMessage[], rid: string, thinking?: boolean): UIMessage[] {
  if (hasPersisted(list, rid, 'assistant')) return list
  if (list.some((m) => m.temp && m.role === 'assistant' && m.request_id === rid)) return list
  return [...list, tempAssistant(rid, thinking)]
}

function patchTemp(list: UIMessage[], rid: string, f: (m: UIMessage) => UIMessage): UIMessage[] {
  return list.map((m) => (m.temp && m.role === 'assistant' && m.request_id === rid ? f(m) : m))
}

/** Promote temp assistant (same request_id) to the persisted Message from done/history. */
function promoteAssistant(list: UIMessage[], msg: Message): UIMessage[] {
  const persisted = fromServer(msg)
  const filtered = list.filter(
    (m) => !(m.id === msg.id || (m.request_id === msg.request_id && m.role === 'assistant')),
  )
  return [...filtered, persisted]
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'reset':
      // optimistic messages typed in the "new chat" view carry over into the freshly created session
      return {
        ...initialChatState,
        sessionId: action.sessionId,
        messages: state.sessionId === null && action.sessionId ? state.messages.filter((m) => m.temp) : [],
      }

    case 'history': {
      // onHistoryLoaded: merge by message_id, persisted replaces temp (same request_id+role).
      // Windowed snapshots may omit older/newer rows we already hold — keep those.
      const server = action.messages.map(fromServer)
      const serverIds = new Set(server.map((m) => m.id!))
      const serverKeys = new Set(server.map((m) => `${m.request_id}:${m.role}`))
      const maxSeq = server.reduce((acc, m) => Math.max(acc, m.sequence_no ?? 0), 0)
      const minSeq = server.reduce(
        (acc, m) => Math.min(acc, m.sequence_no ?? Number.POSITIVE_INFINITY),
        Number.POSITIVE_INFINITY,
      )
      const keep = state.messages.filter((m) => {
        if (m.id && serverIds.has(m.id)) return false
        if (serverKeys.has(`${m.request_id}:${m.role}`)) return false
        if (m.temp) return true
        const seq = m.sequence_no ?? 0
        if (Number.isFinite(minSeq) && seq < minSeq) return true // already-loaded older page
        return seq > maxSeq // persisted from SSE newer than this snapshot
      })
      const merged = sortMessages([...server, ...keep])
      let hasMoreBefore =
        action.hasMoreBefore !== undefined ? action.hasMoreBefore : state.hasMoreBefore
      // A latest-window refetch reports has_more_before against its own oldest seq; if we
      // already hold older rows from loadOlder, keep the prior flag instead of flipping back.
      if (action.hasMoreBefore === true && Number.isFinite(minSeq)) {
        const alreadyHaveOlder = merged.some((m) => (m.sequence_no ?? Number.POSITIVE_INFINITY) < minSeq)
        if (alreadyHaveOlder) hasMoreBefore = state.hasMoreBefore
      }
      return {
        ...state,
        messages: merged,
        historyLoaded: true,
        hasMoreBefore,
      }
    }

    case 'activeRun': {
      if (state.terminal[action.requestId]) return state
      return {
        ...state,
        activeRequestId: action.requestId,
        messages: ensureTemp(state.messages, action.requestId, action.thinking),
      }
    }

    case 'localSend': {
      const optimistic: UIMessage = {
        key: `temp:${action.requestId}:user`,
        request_id: action.requestId,
        role: 'user',
        content: action.content,
        reasoning_content: '',
        sources: [],
        sequence_no: null,
        temp: true,
        status: 'sending',
        notices: [],
      }
      if (state.messages.some((m) => m.request_id === action.requestId && m.role === 'user')) return state
      return { ...state, messages: [...state.messages, optimistic] }
    }

    case 'sendFailed':
      return {
        ...state,
        messages: state.messages.map((m) =>
          m.temp && m.role === 'user' && m.request_id === action.requestId
            ? { ...m, status: 'error', error: { code: action.code, message: action.message } }
            : m,
        ),
      }

    case 'event': {
      const ev = action.event
      // dedupe: drop events at or before the last applied Stream ID (numeric compare)
      if (!isNewer(ev.id, state.lastAppliedId)) return state
      const rid = ev.data.request_id
      let messages = state.messages
      let activeRequestId = state.activeRequestId
      const terminal = state.terminal
      switch (ev.type) {
        case 'start':
          messages = upsertUser(messages, ev.data.user_message, rid)
          if (!terminal[rid]) {
            messages = ensureTemp(messages, rid, ev.data.thinking)
            activeRequestId = rid
          }
          break
        case 'delta':
          if (terminal[rid] || hasPersisted(messages, rid, 'assistant')) break
          messages = ensureTemp(messages, rid)
          messages = patchTemp(messages, rid, (m) =>
            ev.data.kind === 'reasoning'
              ? { ...m, reasoning_content: m.reasoning_content + ev.data.text }
              : { ...m, content: m.content + ev.data.text },
          )
          activeRequestId = rid
          break
        case 'sources':
          messages = patchTemp(ensureTemp(messages, rid), rid, (m) => ({ ...m, sources: ev.data.sources }))
          break
        case 'notice':
          messages = patchTemp(ensureTemp(messages, rid), rid, (m) => ({
            ...m,
            notices: [...m.notices, { code: ev.data.code, message: ev.data.message }],
          }))
          break
        case 'done': {
          if (ev.data.message) {
            messages = promoteAssistant(messages, ev.data.message)
            messages = sortMessages(messages)
          } else {
            messages = patchTemp(messages, rid, (m) => ({
              ...m,
              status: 'done',
              finish_reason: ev.data.finish_reason,
            }))
          }
          if (activeRequestId === rid) activeRequestId = null
          return { ...state, messages, activeRequestId, lastAppliedId: ev.id, terminal: { ...terminal, [rid]: 'done' } }
        }
        case 'error': {
          const interrupted = ev.data.code === 'GENERATION_INTERRUPTED'
          if (!hasPersisted(messages, rid, 'assistant')) messages = ensureTemp(messages, rid)
          messages = patchTemp(messages, rid, (m) => ({
            ...m,
            status: interrupted ? 'interrupted' : 'error',
            error: { code: ev.data.code, message: ev.data.message },
          }))
          if (activeRequestId === rid) activeRequestId = null
          return { ...state, messages, activeRequestId, lastAppliedId: ev.id, terminal: { ...terminal, [rid]: 'error' } }
        }
      }
      return { ...state, messages, activeRequestId, lastAppliedId: ev.id }
    }
  }
}

export const isStreaming = (s: ChatState) => s.activeRequestId !== null

/** Oldest persisted sequence_no in state, or null if none. */
export const oldestSequenceNo = (s: ChatState): number | null => {
  let min: number | null = null
  for (const m of s.messages) {
    if (m.sequence_no == null) continue
    if (min === null || m.sequence_no < min) min = m.sequence_no
  }
  return min
}
