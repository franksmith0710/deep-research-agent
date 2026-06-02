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
      <div v-if="msg.role === 'assistant'" class="message-row">
        <div class="bubble" v-html="render(msg.content)"></div>
      </div>
      <div v-else class="message-row user-row">
        <div class="bubble user-bubble">{{ msg.content }}</div>
      </div>
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
        <svg
          width="14" height="14" viewBox="0 0 14 14" fill="none"
          :style="{ transform: showHistory ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 200ms' }"
        >
          <path d="M5 3l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        {{ showHistory ? '收起详细过程' : `查看详细过程 (${chatStore.allEvents.length} 步)` }}
      </button>
      <div v-if="showHistory || chatStore.runStatus === 'hitl_waiting'" class="thinking-steps">
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
      <div class="message-row">
        <div class="bubble streaming" v-html="render(chatStore.streamingText)"></div>
        <span class="streaming-cursor"></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-history {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 24px 16px;
  scroll-behavior: smooth;
}
.message { margin-bottom: 16px; }
.message-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.user-row {
  flex-direction: row-reverse;
}
.bubble {
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  max-width: 85%;
  line-height: 1.7;
  font-size: 14px;
  color: var(--color-text-primary);
  background: var(--color-assistant-bg);
}
.user-bubble {
  background: var(--color-user-bubble);
  color: white;
  border-radius: var(--radius-lg) var(--radius-lg) var(--radius-xs) var(--radius-lg);
}
.bubble, .user-bubble {
  overflow-wrap: break-word;
  word-wrap: break-word;
}
.bubble :deep(h1) { font-size: 18px; margin: 12px 0 6px; }
.bubble :deep(h2) { font-size: 16px; margin: 10px 0 4px; }
.bubble :deep(h3) { font-size: 15px; margin: 8px 0 4px; }
.bubble :deep(p) { margin: 6px 0; }
.bubble :deep(ul), .bubble :deep(ol) { padding-left: 20px; }
.bubble :deep(li) { margin: 3px 0; }
.bubble :deep(a) { color: var(--color-primary); text-decoration: none; }
.bubble :deep(a):hover { text-decoration: underline; }
.bubble :deep(code) {
  background: rgba(0,0,0,0.06);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}
.bubble :deep(pre) {
  background: rgba(0,0,0,0.05);
  padding: 12px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  margin: 8px 0;
}
.bubble :deep(blockquote) {
  border-left: 3px solid var(--color-border);
  padding-left: 12px;
  color: var(--color-text-secondary);
  margin: 8px 0;
}

.thinking-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
  font-size: 13px;
  color: var(--color-text-secondary);
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
  background: var(--color-text-muted);
  flex-shrink: 0;
}
.thinking-step.latest .step-dot {
  background: var(--color-primary);
  animation: pulse 1.2s ease-in-out infinite;
}
.thinking-step.latest .step-text {
  color: var(--color-primary);
  font-weight: 500;
}

.current-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 14px;
  color: var(--color-primary);
}
.current-step .step-dot.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary);
  animation: pulse 1.2s ease-in-out infinite;
}

.step-history {
  margin-top: 2px;
}
.history-toggle {
  background: none;
  border: none;
  color: var(--color-text-muted);
  font-size: 13px;
  cursor: pointer;
  padding: 6px 0;
  display: flex;
  align-items: center;
  gap: 4px;
  transition: color var(--transition-fast);
}
.history-toggle:hover {
  color: var(--color-primary);
}

@keyframes pulse {
  0%, 100% { opacity: 0.4; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.1); }
}

.streaming-cursor {
  display: inline-block;
  width: 2px;
  height: 16px;
  background: var(--color-primary);
  margin-left: 2px;
  animation: blink 0.8s step-end infinite;
  vertical-align: middle;
  flex-shrink: 0;
}
@keyframes blink {
  50% { opacity: 0; }
}
</style>
