<script setup lang="ts">
defineProps<{ html: string }>()
</script>

<template>
  <div class="report-viewer" v-html="html"></div>
</template>

<style scoped>
.report-viewer { line-height: 1.7; font-size: 14px; }
.report-viewer :deep(h1) { font-size: 20px; margin: 16px 0 8px; }
.report-viewer :deep(h2) { font-size: 17px; margin: 12px 0 6px; color: #64b5f6; }
.report-viewer :deep(p) { margin: 6px 0; }
.report-viewer :deep(ul) { padding-left: 20px; }
.report-viewer :deep(a) { color: #64b5f6; text-decoration: none; }
.report-viewer :deep(sup) { color: #64b5f6; cursor: pointer; font-size: 12px; }
</style>
