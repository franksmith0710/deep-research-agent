<script setup lang="ts">
import { watch, ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { fetchHistory } from '../api'
import ChatHistory from './ChatHistory.vue'
import QueryInput from './QueryInput.vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const queryInputRef = ref<{ abort: () => void }>()

function loadReport(report: string | null) {
  if (!report) return
  const exists = chatStore.messages.some(
    m => m.role === 'assistant' && m.content === report
  )
  if (!exists) {
    chatStore.addMessage({
      id: Date.now(),
      role: 'assistant',
      content: report,
      created_at: new Date().toISOString(),
    })
  }
}

watch(() => sessionStore.currentSessionId, async (id, oldId) => {
  if (!id) return
  if (oldId) queryInputRef.value?.abort()
  chatStore.reset()
  try {
    const data = await fetchHistory(id)
    chatStore.setMessages(data.messages || [])
    loadReport(data.report)
  } catch { /* ignore */ }
})
</script>

<template>
  <main class="main-area">
    <ChatHistory />
    <QueryInput ref="queryInputRef" :hitl="hitl" />
  </main>
</template>

<style scoped>
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  color: #e0e0e0;
  overflow: hidden;
}
</style>
