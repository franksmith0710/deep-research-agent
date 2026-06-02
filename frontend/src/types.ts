export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'hitl' | 'system'
  content: string
  created_at: string
}

export interface ChainEvent {
  type: 'thought' | 'action' | 'action_result'
  node: string
  content: string
  ts: string
}

export interface PatchEvent {
  section: string
  content: string
  append: boolean
  ts: string
}

export interface HITLEvent {
  mode: 'scope_select'
  session_id: string
  options: Record<string, unknown>
  ts: string
}

export interface SessionItem {
  session_id: string
  query_preview: string
  status: string
  updated_at: string
}

export interface SSEEvent {
  event: 'chain' | 'text' | 'patch'
  data: string
}

export interface ResearchRequest {
  query: string
  session_id: string
}

export interface HITLRequest {
  session_id: string
  mode: string
  data: Record<string, unknown>
}
