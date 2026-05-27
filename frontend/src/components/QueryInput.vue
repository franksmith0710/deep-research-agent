<script setup lang="ts">
import { ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { researchSSE, fetchStatus, fetchHistory } from '../api'
import { useChatStore } from '../stores/chatStore'
import { useSSE } from '../composables/useSSE'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ restore: ReturnType<typeof useHITL>['restore'] }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const { handleEvent } = useSSE()

const input = ref('')
const sending = ref(false)
let currentSource: ReturnType<typeof researchSSE> | null = null

function onDone() {
  sending.value = false
  chatStore.finalizeStream()
  currentSource = null
  if (chatStore.pendingQuery) {
    const next = chatStore.pendingQuery
    chatStore.pendingQuery = null
    sendImmediate(next)
  }
}

function abort() {
  currentSource?.abort()
}

defineExpose({ abort })

async function sendImmediate(text: string) {
  sending.value = true
  const sid = sessionStore.currentSessionId
  chatStore.addMessage({
    id: Date.now(),
    role: 'user',
    content: text,
    created_at: new Date().toISOString(),
  })
  currentSource = researchSSE(
    text,
    sid,
    (event, data) => handleEvent(event, data),
    (err) => {
      chatStore.addChainEvent({ type: 'action_result', node: 'error', content: err, ts: '' })
      onDone()
    },
    () => { onDone() }
  )
  chatStore.startStatusPoll(async () => {
    try {
      const st = await fetchStatus(sid)
      if (st.status === 'completed') {
        chatStore.stopStatusPoll()
        chatStore.runStatus = 'completed'
        chatStore.currentStep = ''
        chatStore.isStreaming = false
        chatStore.streamingText = ''
        const d2 = await fetchHistory(sid)
        chatStore.setMessages(d2.messages || [])
      } else if (st.status === 'hitl_waiting') {
        chatStore.runStatus = 'hitl_waiting'
        chatStore.currentStep = ''
        if (st.hitl) {
          props.restore({
            mode: st.hitl.mode as 'scope_select' | 'conflict_resolve' | 'direction_adjust',
            session_id: sid,
            options: st.hitl.options,
            ts: new Date().toISOString(),
          })
        }
      } else if (st.status === 'resuming') {
        chatStore.runStatus = 'running'
        chatStore.currentStep = st.current_step || '正在恢复执行中...'
      } else if (st.status === 'running') {
        chatStore.runStatus = 'running'
        if (!chatStore.isStreaming) {
          chatStore.currentStep = st.current_step
        }
      }
    } catch { /* ignore */ }
  })
}

async function send() {
  const text = input.value.trim()
  if (!text) return
  input.value = ''

  let sid = sessionStore.currentSessionId
  if (!sid) {
    sid = await sessionStore.newSession()
    localStorage.setItem('session_id', sid)
  }

  if (sending.value) {
    chatStore.pendingQuery = text
    return
  }

  await sendImmediate(text)
}
</script>

<template>
  <div class="query-input">
    <input
      v-model="input"
      type="text"
      :disabled="sending || chatStore.runStatus === 'running'"
      :placeholder="chatStore.runStatus === 'running' ? '研究进行中，请等待...' : '输入你的问题...'"
      @keydown.enter="send"
    />
    <button :disabled="sending || !input.trim() || chatStore.runStatus === 'running'" @click="send">
      {{ sending ? '...' : '发送' }}
    </button>
  </div>
</template>

<style scoped>
.query-input {
  display: flex;
  padding: 12px 16px;
  border-top: 1px solid #ddd;
  gap: 8px;
  background: #ffffff;
}
input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #ccc;
  border-radius: 8px;
  background: #ffffff;
  color: #333;
  font-size: 14px;
  outline: none;
}
input:focus { border-color: #1976d2; }
input:disabled { opacity: 0.5; }
button {
  padding: 10px 20px;
  background: #1976d2;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}
button:hover:not(:disabled) { background: #1565c0; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
