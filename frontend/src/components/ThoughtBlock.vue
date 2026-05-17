<script setup lang="ts">
import type { ChainEvent } from '../types'

defineProps<{ event: ChainEvent }>()
</script>

<template>
  <div class="thought-block" :class="event.type">
    <span class="icon">{{ event.type === 'action' ? '⏳' : event.type === 'action_result' ? '✓' : '💭' }}</span>
    <span class="node" v-if="event.node">[{{ event.node }}]</span>
    <span class="content">{{ event.content }}</span>
  </div>
</template>

<style scoped>
.thought-block {
  padding: 6px 12px;
  margin-bottom: 4px;
  font-size: 13px;
}
.thought { color: #999; font-style: italic; }
.action { color: #64b5f6; font-family: monospace; }
.action_result { color: #81c784; }
.icon { margin-right: 6px; }
.node { color: #888; margin-right: 6px; font-size: 11px; }
</style>
