<script setup lang="ts">
import type { SessionItem } from '../types'
import { useSessionStore } from '../stores/sessionStore'

defineProps<{ session: SessionItem }>()
const sessionStore = useSessionStore()
</script>

<template>
  <div
    class="session-item"
    :class="{ active: session.session_id === sessionStore.currentSessionId }"
    @click="sessionStore.setCurrentSession(session.session_id)"
  >
    <div class="query">{{ session.query_preview.slice(0, 20) }}</div>
    <div class="status" :class="session.status">{{ session.status }}</div>
  </div>
</template>

<style scoped>
.session-item {
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}
.session-item:hover { background: #16213e; }
.session-item.active { background: #0f3460; }
.query { font-size: 14px; margin-bottom: 4px; white-space: nowrap; overflow: hidden; }
.status { font-size: 11px; }
.status.completed { color: #4caf50; }
.status.error { color: #f44336; }
.status.running { color: #2196f3; }
</style>
