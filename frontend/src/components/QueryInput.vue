<script setup lang="ts">
import { ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { researchSSE } from '../api'
import { useChatStore } from '../stores/chatStore'
import { useSSE } from '../composables/useSSE'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const { handleEvent } = useSSE()

const input = ref('')
const sending = ref(false)
let currentSource: ReturnType<typeof researchSSE> | null = null

async function send() {
  const text = input.value.trim()
  if (!text || sending.value) return
  input.value = ''
  sending.value = true

  let sid = sessionStore.currentSessionId
  if (!sid) {
    sid = await sessionStore.newSession()
    localStorage.setItem('session_id', sid)
  }

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
      sending.value = false
    },
    () => { sending.value = false }
  )
}
</script>

<template>
  <div class="query-input">
    <input
      v-model="input"
      type="text"
      placeholder="输入你的问题..."
      :disabled="sending"
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
  border-top: 1px solid #333;
  gap: 8px;
}
input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid #444;
  border-radius: 8px;
  background: #1a1a2e;
  color: #e0e0e0;
  font-size: 14px;
  outline: none;
}
input:focus { border-color: #64b5f6; }
input:disabled { opacity: 0.5; }
button {
  padding: 10px 20px;
  background: #0f3460;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}
button:hover:not(:disabled) { background: #16213e; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
