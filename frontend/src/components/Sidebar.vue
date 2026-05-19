<script setup lang="ts">
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import SessionList from './SessionList.vue'

const sessionStore = useSessionStore()
const chatStore = useChatStore()

async function createAndSelect() {
  chatStore.reset()
  const id = await sessionStore.newSession()
  sessionStore.setCurrentSession(id)
}
</script>

<template>
  <aside class="sidebar">
    <button class="new-btn" @click="createAndSelect()">+ 新建会话</button>
    <SessionList />
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  min-width: 280px;
  background: #f5f6f8;
  color: #1a1a1a;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
.new-btn {
  background: #1976d2;
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  margin-bottom: 12px;
}
.new-btn:hover { background: #e8eaee; }
</style>
