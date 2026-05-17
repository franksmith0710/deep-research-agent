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

export async function fetchHistory(sessionId: string): Promise<{
  messages: ChatMessage[]
  report: string | null
}> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/history`)
  return res.json()
}

export function researchSSE(
  query: string,
  sessionId: string,
  onEvent: (event: string, data: string) => void,
  onError: (err: string) => void,
  onDone: () => void,
): EventSource {
  const xhr = new XMLHttpRequest()
  xhr.open('POST', `${BASE}/research`)
  xhr.setRequestHeader('Content-Type', 'application/json')
  xhr.responseType = 'text'

  let lastIndex = 0

  xhr.onprogress = () => {
    const newData = xhr.responseText.slice(lastIndex)
    lastIndex = xhr.responseText.length

    const lines = newData.split('\n')
    let currentEvent = ''
    for (const line of lines) {
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

  xhr.onerror = () => onError('连接失败')
  xhr.onloadend = () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      onDone()
    } else {
      onError(`HTTP ${xhr.status}`)
    }
  }

  xhr.send(JSON.stringify({ query, session_id: sessionId }))

  return {
    abort: () => xhr.abort(),
  } as unknown as EventSource
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
