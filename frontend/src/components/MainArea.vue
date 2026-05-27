<script setup lang="ts">
import { watch, ref } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { fetchHistory, fetchStatus, cancelResearch } from '../api'
import ChatHistory from './ChatHistory.vue'
import QueryInput from './QueryInput.vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const queryInputRef = ref<{ abort: () => void }>()

async function checkRunning(id: string) {
  try {
    const data = await fetchHistory(id)
    chatStore.setMessages(data.messages || [])
    if (data.status === 'running' || data.status === 'hitl_waiting') {
      chatStore.runStatus = data.status
      chatStore.currentStep = '恢复状态中...'
      chatStore.startStatusPoll(async () => {
        try {
          const st = await fetchStatus(id)
          if (st.status === 'completed') {
            chatStore.stopStatusPoll()
            chatStore.runStatus = ''
            chatStore.currentStep = ''
            const d2 = await fetchHistory(id)
            chatStore.setMessages(d2.messages || [])
          } else if (st.status === 'hitl_waiting') {
            chatStore.runStatus = 'hitl_waiting'
            chatStore.currentStep = ''
            if (st.hitl) {
              props.hitl.restore({
                mode: st.hitl.mode as 'scope_select' | 'conflict_resolve' | 'direction_adjust',
                session_id: id,
                options: st.hitl.options,
                ts: new Date().toISOString(),
              })
            }
          } else if (st.status === 'resuming') {
            chatStore.runStatus = 'running'
            chatStore.currentStep = st.current_step || '正在恢复执行中...'
          } else {
            chatStore.runStatus = st.status
            chatStore.currentStep = st.current_step
          }
        } catch { /* ignore */ }
      })
    }
  } catch { /* ignore */ }
}

watch(() => sessionStore.currentSessionId, async (id, oldId) => {
  if (!id) return
  if (oldId) {
    queryInputRef.value?.abort()
    props.hitl.abort()
    cancelResearch(oldId).catch(() => {})
  }
  chatStore.reset()
  await checkRunning(id)
})
</script>

<template>
  <main class="main-area">
    <ChatHistory />
    <QueryInput ref="queryInputRef" :restore="props.hitl.restore" />
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
