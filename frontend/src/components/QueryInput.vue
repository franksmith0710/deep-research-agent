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
  const msgs = chatStore.messages
  if (!msgs.length || msgs[msgs.length - 1].role !== 'assistant') {
    fetchHistory(sessionStore.currentSessionId).then(d => {
      for (const m of (d.messages || [])) {
        if (!chatStore.messages.some(lm => lm.id === m.id)) {
          chatStore.addMessage(m)
        }
      }
    }).catch(() => {})
  }
  if (chatStore.pendingQuery) {
    const next = chatStore.pendingQuery
    chatStore.pendingQuery = null
    sendImmediate(next)
  }
}

function abort() {
  currentSource?.abort()
}

function sendText(text: string) {
  input.value = text
  send()
}

defineExpose({ abort, sendText })

async function sendImmediate(text: string) {
  chatStore.runStatus = ''
  sending.value = true
  const sid = sessionStore.currentSessionId
  chatStore.addMessage({
    id: 'local_' + Date.now(),
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
      if (chatStore.runStatus === 'completed') return
      if (st.status === 'completed') {
        chatStore.stopStatusPoll()
        chatStore.runStatus = 'completed'
        chatStore.currentStep = ''
        chatStore.isStreaming = false
        chatStore.streamingText = ''
        const d2 = await fetchHistory(sid)
        for (const m of (d2.messages || [])) {
          if (!chatStore.messages.some(lm => lm.id === m.id)) {
            chatStore.addMessage(m)
          }
        }
        sessionStore.loadSessions()
      } else if (st.status === 'hitl_waiting') {
        sending.value = false
        currentSource = null
        chatStore.runStatus = 'hitl_waiting'
        chatStore.currentStep = ''
        if (st.hitl) {
          props.restore({
            mode: st.hitl.mode as 'scope_select',
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
  <div class="query-input-wrapper">
    <div class="input-container" :class="{ focused: input.length > 0 || sending }">
      <input
        v-model="input"
        type="text"
        :disabled="sending || chatStore.runStatus === 'running'"
        :placeholder="chatStore.runStatus === 'running' ? '研究进行中，请等待...' : '输入你的问题...'"
        @keydown.enter="send"
      />
      <button
        class="send-btn"
        :disabled="sending || !input.trim() || chatStore.runStatus === 'running'"
        @click="send"
      >
        <svg
          v-if="!sending" width="18" height="18" viewBox="0 0 18 18" fill="none"
        >
          <path d="M2 9l14-7-7 14-2-5-5-2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
        <span v-else class="loading-dots">...</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.query-input-wrapper {
  padding: 12px 16px 16px;
  background: transparent;
}
.input-container {
  display: flex;
  align-items: center;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 4px 4px 4px 16px;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}
.input-container.focused {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
}
input {
  flex: 1;
  border: none;
  background: transparent;
  padding: 10px 0;
  color: var(--color-text-primary);
  font-size: 14px;
  outline: none;
  font-family: inherit;
}
input::placeholder {
  color: var(--color-text-muted);
}
input:disabled {
  opacity: 0.5;
}
.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-primary);
  color: white;
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--transition-fast), opacity var(--transition-fast);
}
.send-btn:hover:not(:disabled) {
  background: var(--color-primary-light);
}
.send-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.loading-dots {
  font-size: 16px;
  letter-spacing: 1px;
}
</style>
