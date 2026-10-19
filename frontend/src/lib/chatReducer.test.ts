import { describe, expect, it } from 'vitest'
import type { Message } from '../api/types'
import { chatReducer, initialChatState, type ChatAction, type ChatState } from './chatReducer'
import { compareStreamIds } from './streamId'
import { sessionsReducer, initialSessions } from './sessionsReducer'
import { linkCitations } from '../components/Markdown'

const RID = 'r1'
const user: Message = {
  id: 'u1', session_id: 's', sequence_no: 1, role: 'user', content: 'hi', reasoning_content: '', sources: [],
  finish_reason: null, request_id: RID, created_at: '2026-01-01T00:00:00Z',
}
const assistant: Message = { ...user, id: 'a1', sequence_no: 2, role: 'assistant', content: 'Hello!', finish_reason: 'stop' }

const run = (actions: ChatAction[], s: ChatState = { ...initialChatState, sessionId: 's' }) =>
  actions.reduce(chatReducer, s)

const start = (id: string): ChatAction => ({
  type: 'event',
  event: { type: 'start', id, data: { request_id: RID, user_message: { id: 'u1', sequence_no: 1, content: 'hi' } } },
})
const delta = (id: string, text: string, kind: 'content' | 'reasoning' = 'content'): ChatAction => ({
  type: 'event', event: { type: 'delta', id, data: { request_id: RID, kind, text } },
})
const done = (id: string): ChatAction => ({
  type: 'event', event: { type: 'done', id, data: { request_id: RID, message_id: 'a1', finish_reason: 'stop' } },
})

describe('stream id (T14)', () => {
  it('compares numerically', () => {
    expect(compareStreamIds('1-9', '1-10')).toBe(-1)
    expect(compareStreamIds('2-0', '1-99')).toBe(1)
    expect(compareStreamIds('3-3', '3-3')).toBe(0)
  })
})

describe('message merge (§9.4)', () => {
  it('builds a temp assistant from deltas and dedupes replayed ids', () => {
    const s = run([start('1-1'), delta('1-2', 'Hel'), delta('1-3', 'lo'), delta('1-2', 'Hel'), delta('1-10', '!')])
    const a = s.messages.filter((m) => m.role === 'assistant')
    expect(a).toHaveLength(1)
    expect(a[0].content).toBe('Hello!')
    expect(s.activeRequestId).toBe(RID)
    expect(s.messages.filter((m) => m.role === 'user')).toHaveLength(1)
  })

  it('T22a: done then history → temp replaced, never two assistants', () => {
    const s = run([start('1-1'), delta('1-2', 'Hello!'), done('1-3'), { type: 'history', messages: [user, assistant] }])
    expect(s.messages.map((m) => [m.role, m.temp])).toEqual([['user', false], ['assistant', false]])
    expect(s.activeRequestId).toBeNull()
  })

  it('done with message promotes temp without history refetch', () => {
    const s = run([
      start('1-1'),
      delta('1-2', 'Hel'),
      delta('1-3', 'lo'),
      {
        type: 'event',
        event: {
          type: 'done',
          id: '1-4',
          data: { request_id: RID, message_id: 'a1', finish_reason: 'stop', message: assistant },
        },
      },
    ])
    expect(s.messages.map((m) => [m.role, m.temp, m.id])).toEqual([
      ['user', false, 'u1'],
      ['assistant', false, 'a1'],
    ])
    expect(s.messages[1].content).toBe('Hello!')
    expect(s.messages[1].status).toBe('done')
    expect(s.activeRequestId).toBeNull()
    expect(s.terminal[RID]).toBe('done')
  })

  it('windowed history keeps already-loaded older messages', () => {
    const older: Message = { ...user, id: 'u0', sequence_no: 1, content: 'old', request_id: 'r0' }
    const olderA: Message = { ...assistant, id: 'a0', sequence_no: 2, content: 'oldA', request_id: 'r0' }
    const newerU: Message = { ...user, id: 'u2', sequence_no: 3, content: 'new', request_id: 'r2' }
    const newerA: Message = { ...assistant, id: 'a2', sequence_no: 4, content: 'newA', request_id: 'r2' }
    let s = run([{ type: 'history', messages: [older, olderA, newerU, newerA], hasMoreBefore: false }])
    // resync latest window omits older pair
    s = chatReducer(s, { type: 'history', messages: [newerU, newerA], hasMoreBefore: true })
    expect(s.messages.map((m) => m.id)).toEqual(['u0', 'a0', 'u2', 'a2'])
    expect(s.hasMoreBefore).toBe(false) // already held older rows
  })

  it('T22b: history already contains final message before replayed deltas arrive', () => {
    const s = run([{ type: 'history', messages: [user, assistant] }, delta('1-2', 'Hel'), done('1-3')])
    expect(s.messages.filter((m) => m.role === 'assistant')).toHaveLength(1)
    expect(s.messages[1].content).toBe('Hello!')
  })

  it('refresh: active run then replay rebuilds temp message', () => {
    const s = run([{ type: 'history', messages: [user] }, { type: 'activeRun', requestId: RID }, delta('1-2', 'par'), delta('1-3', 'tial')])
    expect(s.messages[1].temp).toBe(true)
    expect(s.messages[1].content).toBe('partial')
  })

  it('reasoning and content are separated', () => {
    const s = run([start('1-1'), delta('1-2', 'think', 'reasoning'), delta('1-3', 'answer')])
    expect(s.messages[1].reasoning_content).toBe('think')
    expect(s.messages[1].content).toBe('answer')
  })

  it('error ends streaming and keeps partial output', () => {
    const s = run([start('1-1'), delta('1-2', 'part'), {
      type: 'event', event: { type: 'error', id: '1-3', data: { request_id: RID, code: 'GENERATION_INTERRUPTED', message: '中断' } },
    }])
    expect(s.activeRequestId).toBeNull()
    expect(s.messages[1].status).toBe('interrupted')
    expect(s.messages[1].content).toBe('part')
  })

  it('optimistic user message is replaced by the persisted one', () => {
    const s = run([{ type: 'localSend', requestId: RID, content: 'hi', thinking: false }, start('1-1')])
    const users = s.messages.filter((m) => m.role === 'user')
    expect(users).toHaveLength(1)
    expect(users[0].temp).toBe(false)
  })

  it('new chat: optimistic message survives reset into the created session', () => {
    let s = chatReducer({ ...initialChatState }, { type: 'localSend', requestId: RID, content: 'hi', thinking: false })
    s = chatReducer(s, { type: 'reset', sessionId: 's' })
    expect(s.messages).toHaveLength(1)
    s = chatReducer(s, { type: 'reset', sessionId: 'other' })
    expect(s.messages).toHaveLength(0)
  })

  it('history snapshot older than SSE does not drop the user message from start', () => {
    const s = run([start('1-1'), { type: 'history', messages: [] }])
    expect(s.messages.filter((m) => m.role === 'user')).toHaveLength(1)
  })
})

describe('session list sync (§5.10)', () => {
  const base = { id: 'x', title: '新对话', title_source: 'default' as const, created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z', last_event_id: '0-0', status: 'idle' as const, active_request_id: null, version: 1 }
  it('applies only newer versions; deletions win', () => {
    let s = sessionsReducer(initialSessions, { type: 'loaded', items: [base] })
    s = sessionsReducer(s, { type: 'upsert', session: { ...base, title: '杭州旅行', version: 3 } })
    s = sessionsReducer(s, { type: 'upsert', session: { ...base, title: 'stale', version: 2 } })
    expect(s.byId.x.title).toBe('杭州旅行')
    s = sessionsReducer(s, { type: 'deleted', id: 'x', version: 4 })
    s = sessionsReducer(s, { type: 'upsert', session: { ...base, version: 3 } })
    expect(s.byId.x).toBeUndefined()
    s = sessionsReducer(s, { type: 'loaded', items: [{ ...base, version: 3 }] })
    expect(s.byId.x).toBeUndefined()
  })
})

describe('citations', () => {
  it('links only existing sources', () => {
    const out = linkCitations('事实 [S1]，另一个 [S9]', [{ id: 'S1', title: 't', url: 'https://a.com' }])
    expect(out).toContain('[S1](https://a.com "cite:S1")')
    expect(out).toContain('[S9]')
    expect(out).not.toContain('[S9](')
  })
})
