<script setup lang="ts">
import { watch, ref, nextTick, shallowRef } from 'vue'
import { marked } from 'marked'
import { useChatStore } from '../stores/chatStore'

const chatStore = useChatStore()
const container = ref<HTMLElement>()
const showHistory = ref(false)

let rafId = 0
const throttledScroll = shallowRef(0)

function render(md: string) {
  try {
    return marked.parse(md || '') as string
  } catch {
    return md || ''
  }
}

watch(() => chatStore.streamingText, () => {
  if (!rafId) {
    rafId = requestAnimationFrame(() => {
      rafId = 0
      throttledScroll.value++
    })
  }
})

watch([() => chatStore.messages.length, throttledScroll, () => chatStore.chainEvents.length], async () => {
  await nextTick()
  if (container.value) {
    container.value.scrollTop = container.value.scrollHeight
  }
}, { flush: 'post' })
</script>

<template>
  <div ref="container" class="chat-history">
    <div v-for="msg in chatStore.messages" :key="msg.id" class="message" :class="msg.role">
      <div v-if="msg.role === 'assistant'" class="bubble" v-html="render(msg.content)"></div>
      <div v-else class="bubble">{{ msg.content }}</div>
    </div>

    <div v-if="chatStore.runStatus === 'hitl_waiting'" class="current-step">
      <span class="step-dot pulse"></span>
      <span class="step-text">等待用户确认中...</span>
    </div>

    <div v-else-if="chatStore.currentEvent" class="current-step">
      <span class="step-dot pulse"></span>
      <span class="step-text">{{ chatStore.currentEvent.content }}</span>
    </div>

    <div v-else-if="chatStore.runStatus === 'running' && chatStore.currentStep" class="current-step">
      <span class="step-dot pulse"></span>
      <span class="step-text">{{ chatStore.currentStep }}</span>
    </div>

    <div v-if="chatStore.allEvents.length > 1" class="step-history">
      <button class="history-toggle" @click="showHistory = !showHistory">
        {{ showHistory ? '收起详细过程' : `查看详细过程 (${chatStore.allEvents.length} 步)` }}
      </button>
      <div v-if="showHistory" class="thinking-steps">
        <div
          v-for="(ev, i) in chatStore.allEvents"
          :key="i"
          class="thinking-step"
          :class="{ latest: i === chatStore.allEvents.length - 1 && chatStore.runStatus === 'running' }"
        >
          <span class="step-dot"></span>
          <span class="step-text">{{ ev.content }}</span>
        </div>
      </div>
    </div>

    <div v-if="chatStore.isStreaming" class="message assistant">
      <div class="bubble streaming" v-html="render(chatStore.streamingText)"></div>
    </div>

    <div v-if="chatStore.isStreaming" class="streaming-cursor"></div>
  </div>
</template>

<style scoped>
.chat-history {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.message { margin-bottom: 12px; }
.message.user { text-align: right; }
.message.user .bubble {
  background: #1976d2;
  color: white;
  display: inline-block;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  max-width: 80%;
  text-align: left;
}
.message.assistant .bubble {
  background: #f0f0f4;
  color: #1a1a1a;
  display: block;
  padding: 12px 16px;
  border-radius: 12px;
  max-width: 100%;
  line-height: 1.7;
  font-size: 14px;
  border: 1px solid #e0e0e0;
}
.message.assistant .bubble :deep(h1) { font-size: 18px; margin: 12px 0 6px; color: #1a1a1a; }
.message.assistant .bubble :deep(h2) { font-size: 16px; margin: 10px 0 4px; color: #1976d2; }
.message.assistant .bubble :deep(p) { margin: 6px 0; }
.message.assistant .bubble :deep(ul) { padding-left: 20px; }
.message.assistant .bubble :deep(a) { color: #1976d2; text-decoration: none; }

.thinking-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
  font-size: 14px;
  color: #666;
}
.thinking-step {
  display: flex;
  align-items: center;
  gap: 8px;
  line-height: 1.4;
}
.step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #bbb;
  flex-shrink: 0;
}
.thinking-step.latest .step-dot {
  background: #1976d2;
  animation: pulse 1.2s ease-in-out infinite;
}
.thinking-step.latest .step-text {
  color: #1976d2;
  font-weight: 500;
}

.current-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  font-size: 14px;
  color: #1976d2;
}
.current-step .step-dot.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #1976d2;
  animation: pulse 1.2s ease-in-out infinite;
}
.current-step .step-dot.done {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4caf50;
}

.step-history {
  margin-top: 4px;
}
.history-toggle {
  background: none;
  border: none;
  color: #888;
  font-size: 13px;
  cursor: pointer;
  padding: 4px 0;
  text-decoration: underline;
}
.history-toggle:hover {
  color: #1976d2;
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.1); }
}

.streaming-cursor {
  display: inline-block;
  width: 8px;
  height: 16px;
  background: #1976d2;
  margin-left: 8px;
  animation: blink 0.8s step-end infinite;
  vertical-align: middle;
}
@keyframes blink {
  50% { opacity: 0; }
}
</style>