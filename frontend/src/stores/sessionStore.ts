import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { SessionItem } from '../types'
import { fetchSessions, createSession } from '../api'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref<SessionItem[]>([])
  const currentSessionId = ref<string>('')
  const loading = ref(false)

  async function loadSessions() {
    try {
      sessions.value = await fetchSessions()
    } catch {
      sessions.value = []
    }
  }

  async function newSession() {
    const id = await createSession()
    currentSessionId.value = id
    await loadSessions()
    return id
  }

  function setCurrentSession(id: string) {
    currentSessionId.value = id
  }

  return { sessions, currentSessionId, loading, loadSessions, newSession, setCurrentSession }
})
