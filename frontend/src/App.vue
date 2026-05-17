<script setup lang="ts">
import { onMounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import MainArea from './components/MainArea.vue'
import HITLDialog from './components/HITLDialog.vue'
import { useSessionStore } from './stores/sessionStore'
import { useChatStore } from './stores/chatStore'
import { useHITL } from './composables/useHITL'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const hitl = useHITL()

onMounted(async () => {
  await sessionStore.loadSessions()
  const saved = localStorage.getItem('session_id')
  if (saved) {
    sessionStore.setCurrentSession(saved)
  }
})
</script>

<template>
  <div class="app-container">
    <Sidebar />
    <MainArea :hitl="hitl" />
    <HITLDialog v-if="hitl.showDialog.value" :hitl="hitl" />
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
.app-container { display: flex; height: 100vh; overflow: hidden; }
</style>
