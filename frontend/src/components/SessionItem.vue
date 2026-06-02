<script setup lang="ts">
import type { SessionItem } from '../types'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { deleteSession } from '../api'

const props = defineProps<{ session: SessionItem }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()

const isActive = () => props.session.session_id === sessionStore.currentSessionId

async function handleDelete(e: Event) {
  e.stopPropagation()
  if (!confirm('确定要删除这个会话吗？')) return
  const active = isActive()
  await deleteSession(props.session.session_id)
  sessionStore.loadSessions()
  if (active) {
    chatStore.reset()
    sessionStore.setCurrentSession('')
  }
}

function statusColor(status: string) {
  if (status === 'completed') return 'var(--color-success)'
  if (status === 'error') return 'var(--color-error)'
  if (status === 'running') return 'var(--color-running)'
  return 'var(--color-text-muted)'
}
</script>

<template>
  <div
    class="session-item"
    :class="{ active: isActive() }"
    @click="sessionStore.setCurrentSession(session.session_id)"
  >
    <div class="item-content">
      <div class="query">{{ session.query_preview.slice(0, 20) }}</div>
      <div class="meta">
        <span class="status-dot" :style="{ background: statusColor(session.status) }"></span>
        <span class="status-label">{{ session.status }}</span>
      </div>
    </div>
    <button class="del-btn" @click="handleDelete" title="删除">
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 4h10M5 4V2.5A.5.5 0 015.5 2h3a.5.5 0 01.5.5V4m-6 6V6m4 4V6M3 4l.667 7.333A1 1 0 004.667 12h4.666a1 1 0 001-.667L11 4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
  </div>
</template>

<style scoped>
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  margin: 0 8px 2px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  position: relative;
  transition: background var(--transition-fast);
}
.session-item:hover {
  background: var(--color-assistant-bg);
}
.session-item.active {
  background: var(--color-primary-bg);
  box-shadow: inset 3px 0 0 0 var(--color-primary);
}
.item-content {
  flex: 1;
  min-width: 0;
}
.query {
  font-size: 14px;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}
.meta {
  display: flex;
  align-items: center;
  gap: 4px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-label {
  font-size: 11px;
  color: var(--color-text-muted);
  text-transform: capitalize;
}
.del-btn {
  background: none;
  border: none;
  color: var(--color-text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: none;
  flex-shrink: 0;
  transition: color var(--transition-fast);
}
.session-item:hover .del-btn { display: flex; }
.del-btn:hover { color: var(--color-error); background: rgba(239,68,68,0.08); }
</style>
