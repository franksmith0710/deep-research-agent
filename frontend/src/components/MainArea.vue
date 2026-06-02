<script setup lang="ts">
import { watch, ref, computed } from 'vue'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { fetchHistory, fetchStatus } from '../api'
import ChatHistory from './ChatHistory.vue'
import QueryInput from './QueryInput.vue'
import WelcomeSuggestions from './WelcomeSuggestions.vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const queryInputRef = ref<{ abort: () => void; sendText: (t: string) => void }>()

const showWelcome = computed(() =>
  !!sessionStore.currentSessionId &&
  chatStore.messages.length === 0 &&
  !chatStore.isStreaming &&
  chatStore.runStatus !== 'running'
)

function handleSelect(text: string) {
  queryInputRef.value?.sendText(text)
}

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
          if (chatStore.runStatus === 'completed') return
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
                mode: st.hitl.mode as 'scope_select',
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
  }
  chatStore.reset()
  await checkRunning(id)
})
</script>

<template>
  <main class="main-area">
    <WelcomeSuggestions
      v-if="showWelcome"
      :on-select="handleSelect"
    />
    <ChatHistory v-else />
    <QueryInput ref="queryInputRef" :restore="props.hitl.restore" />
  </main>
</template>

<style scoped>
.main-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--color-bg);
  overflow: hidden;
}
</style>
