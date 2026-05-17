<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { useReportStore } from '../stores/reportStore'
import { fetchHistory } from '../api'
import ChatHistory from './ChatHistory.vue'
import ReportBlock from './ReportBlock.vue'
import QueryInput from './QueryInput.vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const reportStore = useReportStore()

watch(() => sessionStore.currentSessionId, async (id) => {
  if (!id) return
  chatStore.reset()
  reportStore.reset()
  try {
    const data = await fetchHistory(id)
    chatStore.setMessages(data.messages || [])
    if (data.report) reportStore.setReport(data.report)
  } catch { /* ignore */ }
})

onMounted(() => {
  if (sessionStore.currentSessionId) {
    fetchHistory(sessionStore.currentSessionId).then(data => {
      chatStore.setMessages(data.messages || [])
      if (data.report) reportStore.setReport(data.report)
    })
  }
})
</script>

<template>
  <main class="main-area">
    <ChatHistory />
    <ReportBlock :report="reportStore.report.content" />
    <QueryInput />
  </main>
</template>

<style scoped>
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #0a0a1a;
  color: #e0e0e0;
  overflow: hidden;
}
</style>
