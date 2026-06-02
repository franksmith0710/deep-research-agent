<script setup lang="ts">
import { useSessionStore } from '../stores/sessionStore'
import SessionItem from './SessionItem.vue'

const sessionStore = useSessionStore()
</script>

<template>
  <div class="session-list">
    <div v-for="s in sessionStore.sessions" :key="s.session_id">
      <SessionItem :session="s" />
    </div>
    <div v-if="sessionStore.sessions.length === 0" class="empty">暂无会话</div>
  </div>
</template>

<style scoped>
.session-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.empty { color: var(--color-text-muted); font-size: 13px; padding: 24px 16px; text-align: center; }
</style>
