import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, ChainEvent } from '../types'
import { useReportStore } from './reportStore'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const chainEvents = ref<ChainEvent[]>([])
  const loading = ref(false)

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function addChainEvent(event: ChainEvent) {
    chainEvents.value.push(event)
  }

  function setMessages(msgs: ChatMessage[]) {
    messages.value = msgs
  }

  function reset() {
    messages.value = []
    chainEvents.value = []
    const reportStore = useReportStore()
    reportStore.reset()
  }

  return {
    messages, chainEvents, loading,
    addMessage, addChainEvent, setMessages, reset,
  }
})
