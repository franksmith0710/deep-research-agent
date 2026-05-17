<script setup lang="ts">
import { useSessionStore } from '../stores/sessionStore'
import SessionList from './SessionList.vue'

const sessionStore = useSessionStore()
</script>

<template>
  <aside class="sidebar">
    <button class="new-btn" @click="sessionStore.newSession()">+ 新建会话</button>
    <SessionList />
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  min-width: 280px;
  background: #1a1a2e;
  color: #e0e0e0;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
.new-btn {
  background: #0f3460;
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  margin-bottom: 12px;
}
.new-btn:hover { background: #16213e; }
</style>
