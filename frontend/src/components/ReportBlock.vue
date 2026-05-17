<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import ReportViewer from './ReportViewer.vue'

const props = defineProps<{ report: string }>()
const html = computed(() => marked(props.report || ''))
</script>

<template>
  <div v-if="report" class="report-block">
    <div class="report-header">研究报告</div>
    <ReportViewer :html="html" />
  </div>
</template>

<style scoped>
.report-block {
  border-top: 1px solid #333;
  padding: 16px;
  max-height: 50vh;
  overflow-y: auto;
  background: #0d0d1f;
}
.report-header {
  font-size: 13px;
  color: #888;
  margin-bottom: 8px;
  font-weight: bold;
}
</style>
