import type { SessionItem, ChatMessage } from '../types'

const BASE = '/api'

export async function fetchSessions(): Promise<SessionItem[]> {
  const res = await fetch(`${BASE}/sessions`)
  const data = await res.json()
  return data.sessions || []
}

export async function createSession(): Promise<string> {
  const res = await fetch(`${BASE}/sessions`, { method: 'POST' })
  const data = await res.json()
  return data.session_id
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`${BASE}/sessions/${sessionId}`, { method: 'DELETE' })
}

export async function fetchHistory(sessionId: string): Promise<{
  messages: ChatMessage[]
  report: string | null
}> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/history`)
  return res.json()
}

interface SSEAbortable {
  abort: () => void
}

export function researchSSE(
  query: string,
  sessionId: string,
  onEvent: (event: string, data: string) => void,
  onError: (err: string) => void,
  onDone: () => void,
): SSEAbortable {
  const xhr = new XMLHttpRequest()
  xhr.open('POST', `${BASE}/research`)
  xhr.setRequestHeader('Content-Type', 'application/json')
  xhr.responseType = 'text'

  let buffer = ''
  let done = false

  xhr.onprogress = () => {
    buffer += xhr.responseText.slice(buffer.length)
    const parts = buffer.split('\n')
    buffer = parts.pop() || ''
    let currentEvent = ''
    for (const line of parts) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        const data = line.slice(6).trim()
        if (currentEvent && data) {
          onEvent(currentEvent, data)
        }
      }
    }
  }

  xhr.onerror = () => {
    if (done) return
    done = true
    onError('连接失败')
  }
  xhr.onloadend = () => {
    if (done) return
    done = true
    if (xhr.status >= 200 && xhr.status < 300) {
      onDone()
    } else {
      onError(`HTTP ${xhr.status}`)
    }
  }

  xhr.send(JSON.stringify({ query, session_id: sessionId }))

  return { abort: () => xhr.abort() }
}

export async function cancelResearch(sessionId: string): Promise<void> {
  await fetch(`${BASE}/research/${sessionId}/cancel`, { method: 'POST' })
}

export async function submitHITL(
  sessionId: string,
  mode: string,
  data: Record<string, unknown>,
): Promise<void> {
  await fetch(`${BASE}/hitl/callback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, mode, data }),
  })
}
