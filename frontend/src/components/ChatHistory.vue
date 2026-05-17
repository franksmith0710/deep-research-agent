<script setup lang="ts">
import { useChatStore } from '../stores/chatStore'
import ThoughtBlock from './ThoughtBlock.vue'

const chatStore = useChatStore()
</script>

<template>
  <div class="chat-history">
    <div v-for="msg in chatStore.messages" :key="msg.id" class="message" :class="msg.role">
      <div class="bubble">{{ msg.content }}</div>
    </div>
    <div v-for="(ev, i) in chatStore.chainEvents" :key="i">
      <ThoughtBlock :event="ev" />
    </div>
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
  background: #0f3460;
  color: white;
  display: inline-block;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  max-width: 70%;
  text-align: left;
}
.message.assistant .bubble {
  background: #1a1a2e;
  color: #e0e0e0;
  display: inline-block;
  padding: 10px 16px;
  border-radius: 16px 16px 16px 4px;
  max-width: 70%;
}
</style>
