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
    <div class="sidebar-header">
      <div class="logo">Deep Search</div>
      <button class="new-btn" @click="createAndSelect()">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2v12m-6-6h12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        新建会话
      </button>
    </div>
    <SessionList />
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  min-width: var(--sidebar-width);
  background: var(--color-surface);
  color: var(--color-text-primary);
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--color-sidebar-border);
}
.sidebar-header {
  padding: 16px 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-bottom: 1px solid var(--color-border-light);
  margin-bottom: 4px;
}
.logo {
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: -0.3px;
  padding: 0 4px;
}
.new-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: var(--color-primary);
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: background var(--transition-fast), transform var(--transition-fast);
}
.new-btn:hover {
  background: var(--color-primary-light);
  transform: translateY(-1px);
}
.new-btn:active {
  transform: translateY(0);
}
</style>
