<script setup lang="ts">
import { watch, ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { fetchHistory, cancelResearch } from '../api'
import ChatHistory from './ChatHistory.vue'
import QueryInput from './QueryInput.vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const queryInputRef = ref<{ abort: () => void }>()

watch(() => sessionStore.currentSessionId, async (id, oldId) => {
  if (!id) return
  if (oldId) {
    queryInputRef.value?.abort()
    cancelResearch(oldId).catch(() => {})
  }
  chatStore.reset()
  try {
    const data = await fetchHistory(id)
    chatStore.setMessages(data.messages || [])
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
