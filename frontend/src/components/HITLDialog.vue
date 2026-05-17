<script setup lang="ts">
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()
</script>

<template>
  <div class="hitl-overlay" @click.self="props.hitl.close()">
    <div class="hitl-dialog">
      <div class="hitl-header" v-if="props.hitl.currentHITL.value">
        HITL: {{ props.hitl.currentHITL.value.mode }}
      </div>
      <div class="hitl-body">
        <p v-if="props.hitl.currentHITL.value?.mode === 'scope_select'">
          请选择调研范围：
        </p>
        <p v-else-if="props.hitl.currentHITL.value?.mode === 'conflict_resolve'">
          存在信息冲突，请选择采信方案：
        </p>
        <p v-else-if="props.hitl.currentHITL.value?.mode === 'outline_edit'">
          请调整报告大纲：
        </p>
        <p v-else>{{ JSON.stringify(props.hitl.currentHITL.value?.options) }}</p>
      </div>
      <div class="hitl-footer">
        <button @click="props.hitl.close()">确认</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hitl-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.hitl-dialog {
  background: #1a1a2e;
  border: 1px solid #444;
  border-radius: 12px;
  padding: 24px;
  min-width: 400px;
  max-width: 600px;
  color: #e0e0e0;
}
.hitl-header { font-size: 16px; font-weight: bold; margin-bottom: 16px; }
.hitl-body { margin-bottom: 20px; line-height: 1.6; }
.hitl-footer { text-align: right; }
.hitl-footer button {
  padding: 8px 24px;
  background: #0f3460;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
</style>
