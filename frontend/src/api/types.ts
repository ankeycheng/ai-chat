export type SessionStatus = 'idle' | 'generating'

export interface SearchMatch {
  field: 'title' | 'message'
  snippet: string
  message_id?: string | null
  role?: string | null
}

export interface Session {
  id: string
  title: string
  title_source: 'default' | 'auto' | 'user'
  created_at: string
  updated_at: string
  last_event_id: string
  status: SessionStatus
  active_request_id: string | null
  version: number
  match?: SearchMatch | null
}

export interface Source {
  id: string
  title: string
  url: string
  snippet?: string
  site_name?: string | null
  site_icon?: string | null
  date?: string | null
}

export interface Message {
  id: string
  session_id: string
  sequence_no: number
  role: 'user' | 'assistant'
  content: string
  reasoning_content: string
  sources: Source[]
  finish_reason: string | null
  request_id: string
  created_at: string
}

export interface ActiveRun {
  active: boolean
  request_id?: string | null
  status?: string | null
  start_event_id?: string | null
  thinking?: boolean | null
  web_search?: boolean | null
  user_message_id?: string | null
  last_event_id?: string | null
}

export interface ApiErrorBody {
  error: { code: string; message: string; request_id?: string }
}

export interface MessageList {
  items: Message[]
  oldest_sequence_no: number | null
  newest_sequence_no: number | null
  has_more_before: boolean
}

export interface MessagesQuery {
  after_sequence_no?: number
  before_sequence_no?: number
  limit?: number
}
