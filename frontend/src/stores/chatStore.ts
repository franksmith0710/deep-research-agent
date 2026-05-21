import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, ChainEvent } from '../types'
import { useReportStore } from './reportStore'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const allEvents = ref<ChainEvent[]>([])
  const currentEvent = ref<ChainEvent | null>(null)
  const chainEvents = allEvents
  const loading = ref(false)
  const streamingText = ref('')
  const isStreaming = ref(false)

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function addChainEvent(event: ChainEvent) {
    allEvents.value.push(event)
    currentEvent.value = event
  }

  function setMessages(msgs: ChatMessage[]) {
    const existing = new Set(messages.value.map(m => m.id))
    for (const msg of msgs) {
      if (!existing.has(msg.id)) {
        messages.value.push(msg)
      }
    }
  }

  function appendStream(chunk: string) {
    if (!isStreaming.value) {
      isStreaming.value = true
      streamingText.value = chunk
    } else {
      streamingText.value += chunk
    }
  }

  function finalizeStream() {
    if (!streamingText.value) return
    messages.value.push({
      id: Date.now(),
      role: 'assistant',
      content: streamingText.value,
      created_at: new Date().toISOString(),
    })
    streamingText.value = ''
    isStreaming.value = false
    allEvents.value = []
    currentEvent.value = null
  }

  function reset() {
    messages.value = []
    allEvents.value = []
    currentEvent.value = null
    streamingText.value = ''
    isStreaming.value = false
    const reportStore = useReportStore()
    reportStore.reset()
  }

  return {
    messages, chainEvents, allEvents, currentEvent, loading, streamingText, isStreaming,
    addMessage, addChainEvent, setMessages, appendStream, finalizeStream, reset,
  }
})
