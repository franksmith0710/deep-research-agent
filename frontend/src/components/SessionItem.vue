<script setup lang="ts">
import type { SessionItem } from '../types'
import { useSessionStore } from '../stores/sessionStore'
import { useChatStore } from '../stores/chatStore'
import { deleteSession } from '../api'

const props = defineProps<{ session: SessionItem }>()
const sessionStore = useSessionStore()
const chatStore = useChatStore()

async function handleDelete(e: Event) {
  e.stopPropagation()
  if (!confirm('确定要删除这个会话吗？')) return
  const isActive = sessionStore.currentSessionId === props.session.session_id
  await deleteSession(props.session.session_id)
  sessionStore.loadSessions()
  if (isActive) {
    chatStore.reset()
    sessionStore.setCurrentSession('')
  }
}
</script>

<template>
  <div
    class="session-item"
    :class="{ active: session.session_id === sessionStore.currentSessionId }"
    @click="sessionStore.setCurrentSession(session.session_id)"
  >
    <div class="query">{{ session.query_preview.slice(0, 20) }}</div>
    <div class="row">
      <div class="status" :class="session.status">{{ session.status }}</div>
      <button class="del-btn" title="删除" @click="handleDelete">×</button>
    </div>
  </div>
</template>

<style scoped>
.session-item {
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}
.session-item:hover { background: #e8eaee; }
.session-item.active { background: #1976d2; color: white; }
.session-item.active .query, .session-item.active .status { color: white; }
.query { font-size: 14px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; color: #333; }
.row { display: flex; align-items: center; justify-content: space-between; }
.status { font-size: 11px; }
.status.completed { color: #4caf50; }
.status.error { color: #f44336; }
.status.running { color: #2196f3; }
.del-btn {
  background: none;
  border: none;
  color: #999;
  font-size: 18px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  display: none;
}
.session-item:hover .del-btn { display: block; }
.del-btn:hover { color: #f44336; }
</style>
