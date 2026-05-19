<script setup lang="ts">
import { useSessionStore } from '../stores/sessionStore'
import SessionItem from './SessionItem.vue'

const sessionStore = useSessionStore()
</script>

<template>
  <div class="session-list">
    <div class="list-header">历史会话</div>
    <div v-for="s in sessionStore.sessions" :key="s.session_id">
      <SessionItem :session="s" />
    </div>
    <div v-if="sessionStore.sessions.length === 0" class="empty">暂无会话</div>
  </div>
</template>

<style scoped>
.session-list { flex: 1; overflow-y: auto; }
.list-header { font-size: 12px; color: #999; padding: 8px 4px; }
.empty { color: #999; font-size: 13px; padding: 16px; text-align: center; }
</style>
