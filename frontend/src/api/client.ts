import type { ActiveRun, ApiErrorBody, MessageList, MessagesQuery, Session } from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(API_BASE + path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '网络连接失败，请检查网络后重试')
  }
  if (resp.status === 204) return undefined as T
  const text = await resp.text()
  const body = text ? JSON.parse(text) : undefined
  if (!resp.ok) {
    const err = (body as ApiErrorBody | undefined)?.error
    throw new ApiError(resp.status, err?.code ?? 'HTTP_' + resp.status, err?.message ?? '请求失败')
  }
  return body as T
}

export const api = {
  listSessions: (q?: string, limit = 100) =>
    request<{ items: Session[]; next_cursor: string | null }>(
      `/api/sessions?limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ''}`,
    ),
  createSession: (title?: string) =>
    request<Session>('/api/sessions', { method: 'POST', body: JSON.stringify(title ? { title } : {}) }),
  getSession: (sid: string) => request<Session>(`/api/sessions/${sid}`),
  renameSession: (sid: string, title: string) =>
    request<Session>(`/api/sessions/${sid}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  deleteSession: (sid: string) => request<void>(`/api/sessions/${sid}`, { method: 'DELETE' }),
  messages: (sid: string, opts?: MessagesQuery) => {
    const q = new URLSearchParams()
    if (opts?.after_sequence_no != null) q.set('after_sequence_no', String(opts.after_sequence_no))
    if (opts?.before_sequence_no != null) q.set('before_sequence_no', String(opts.before_sequence_no))
    if (opts?.limit != null) q.set('limit', String(opts.limit))
    const qs = q.toString()
    return request<MessageList>(`/api/sessions/${sid}/messages${qs ? `?${qs}` : ''}`)
  },
  activeRun: (sid: string) => request<ActiveRun>(`/api/sessions/${sid}/active-run`),
  send: (sid: string, body: { content: string; thinking: boolean; web_search: boolean; request_id: string }) =>
    request<{ accepted: boolean; request_id: string; status: string }>(`/api/sessions/${sid}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

export const streamUrl = (sid: string, after?: string | null) =>
  `${API_BASE}/api/sessions/${sid}/stream${after ? `?after=${encodeURIComponent(after)}` : ''}`

export const globalEventsUrl = () => `${API_BASE}/api/events`
