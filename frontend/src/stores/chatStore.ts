import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, ChainEvent } from '../types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const allEvents = ref<ChainEvent[]>([])
  const currentEvent = ref<ChainEvent | null>(null)
  const chainEvents = allEvents
  const loading = ref(false)
  const streamingText = ref('')
  const isStreaming = ref(false)

  // 刷新后恢复的后台运行状态
  const runStatus = ref('')       // '' | 'running' | 'hitl_waiting' | 'completed'
  const currentStep = ref('')     // 当前步骤描述（如 "正在搜索相关信息..."）
  const pendingQuery = ref<string | null>(null)  // 排队等待发送的查询
  let statusPollTimer: ReturnType<typeof setInterval> | null = null

  function startStatusPoll(onStatus: (s: string) => void) {
    stopStatusPoll()
    statusPollTimer = setInterval(onStatus, 3000)
  }

  function stopStatusPoll() {
    if (statusPollTimer !== null) {
      clearInterval(statusPollTimer)
      statusPollTimer = null
    }
  }

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function addChainEvent(event: ChainEvent) {
    allEvents.value.push(event)
    currentEvent.value = event
  }

  function setMessages(msgs: ChatMessage[]) {
    messages.value = msgs
  }

  function appendStream(chunk: string) {
    if (!isStreaming.value) {
      isStreaming.value = true
      streamingText.value = ''
      allEvents.value = []
      currentEvent.value = null
      currentStep.value = ''
    }
    streamingText.value += chunk
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
    currentStep.value = ''
  }

  function clearPending() {
    pendingQuery.value = null
  }

  function reset() {
    messages.value = []
    allEvents.value = []
    currentEvent.value = null
    streamingText.value = ''
    isStreaming.value = false
    runStatus.value = ''
    currentStep.value = ''
    pendingQuery.value = null
    stopStatusPoll()
  }

  return {
    messages, chainEvents, allEvents, currentEvent, loading, streamingText, isStreaming,
    runStatus, currentStep, pendingQuery,
    addMessage, addChainEvent, setMessages, appendStream, finalizeStream, reset, clearPending,
    startStatusPoll, stopStatusPoll,
  }
})
