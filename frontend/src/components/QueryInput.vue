<script setup lang="ts">
import { ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { researchSSE } from '../api'
import { useChatStore } from '../stores/chatStore'
import { useSSE } from '../composables/useSSE'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const { handleEvent } = useSSE(props.hitl)

const input = ref('')
const sending = ref(false)
const pending = ref<string | null>(null)
let currentSource: ReturnType<typeof researchSSE> | null = null

function onDone() {
  props.hitl.setSSEActive(false)
  props.hitl.close()
  sending.value = false
  chatStore.finalizeStream()
  currentSource = null
  if (pending.value) {
    const next = pending.value
    pending.value = null
    sendImmediate(next)
  }
}

function abort() {
  currentSource?.abort()
}

defineExpose({ abort })

async function sendImmediate(text: string) {
  props.hitl.setSSEActive(true)
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
    pending.value = text
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
      placeholder="输入你的问题..."
      @keydown.enter="send"
    />
    <button :disabled="sending || !input.trim()" @click="send">
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
