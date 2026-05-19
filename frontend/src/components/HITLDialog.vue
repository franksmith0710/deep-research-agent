<script setup lang="ts">
import { ref, computed } from 'vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()

const selectedOptions = ref<string[]>([])
const selectedChoice = ref<string>('')
const outlineText = ref<string>('')
const submitting = ref(false)

const hitlMode = computed(() => props.hitl.currentHITL.value?.mode as string | undefined)

async function confirm() {
  if (submitting.value) return
  submitting.value = true
  try {
    let data: Record<string, unknown> = {}
    if (hitlMode.value === 'scope_select') {
      data = { selectedDimensions: selectedOptions.value }
    } else if (hitlMode.value === 'conflict_resolve') {
      data = { selectedChoice: selectedChoice.value }
    } else if (hitlMode.value === 'outline_edit') {
      data = { outline: outlineText.value }
    } else if (hitlMode.value === 'direction_adjust') {
      data = { selectedSubQueries: selectedOptions.value }
    }
    resetInputs()
    await props.hitl.submit(data)
  } finally {
    submitting.value = false
  }
}

function resetInputs() {
  selectedOptions.value = []
  selectedChoice.value = ''
  outlineText.value = ''
}
</script>

<template>
  <div class="hitl-overlay" @click.self="props.hitl.close()">
    <div class="hitl-dialog">
      <div class="hitl-header">需要你的确认</div>
      <div class="hitl-body">
        <template v-if="hitlMode === 'scope_select'">
          <p>请选择感兴趣的调研维度（可多选）：</p>
          <div class="options">
            <label v-for="dim in (props.hitl.currentHITL.value?.options?.dimensions as string[] || [])" :key="dim">
              <input type="checkbox" :value="dim" v-model="selectedOptions" /> {{ dim }}
            </label>
          </div>
        </template>

        <template v-else-if="hitlMode === 'conflict_resolve'">
          <p>存在信息冲突，请选择采信方案：</p>
          <div class="options">
            <label>
              <input type="radio" value="left" v-model="selectedChoice" /> 采信方案一
            </label>
            <label>
              <input type="radio" value="right" v-model="selectedChoice" /> 采信方案二
            </label>
            <label>
              <input type="radio" value="both" v-model="selectedChoice" /> 双方并列采信
            </label>
          </div>
        </template>

        <template v-else-if="hitlMode === 'direction_adjust'">
          <p>是否需要调整搜索方向？可多选更精确的查询：</p>
          <div class="options">
            <label v-for="(sq, i) in (props.hitl.currentHITL.value?.options?.sub_queries as string[] || [])" :key="i">
              <input type="checkbox" :value="sq" v-model="selectedOptions" /> {{ sq }}
            </label>
          </div>
        </template>

        <template v-else-if="hitlMode === 'outline_edit'">
          <p>请调整报告大纲：</p>
          <textarea v-model="outlineText" rows="6" class="outline-input" />
        </template>

        <template v-else>
          <p>{{ JSON.stringify(props.hitl.currentHITL.value?.options) }}</p>
        </template>
      </div>
      <div class="hitl-footer">
        <button class="cancel" @click="props.hitl.close()">取消</button>
        <button class="confirm" :disabled="submitting" @click="confirm">
          {{ submitting ? '提交中...' : '确认' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.hitl-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.hitl-dialog {
  background: #ffffff;
  border: 1px solid #e0e0e0;
  border-radius: 12px;
  padding: 24px;
  min-width: 400px;
  max-width: 600px;
  color: #333;
  box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.hitl-header { font-size: 16px; font-weight: bold; margin-bottom: 16px; }
.hitl-body { margin-bottom: 20px; line-height: 1.6; }
.hitl-body .options label {
  display: block;
  padding: 6px 0;
  cursor: pointer;
}
.hitl-body .options input { margin-right: 8px; }
.outline-input {
  width: 100%;
  padding: 8px;
  background: #f5f6f8;
  color: #333;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 13px;
  resize: vertical;
}
.hitl-footer { text-align: right; display: flex; gap: 8px; justify-content: flex-end; }
.hitl-footer button {
  padding: 8px 24px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}
.hitl-footer .cancel {
  background: #e8e8e8;
  color: #333;
}
.hitl-footer .confirm {
  background: #1976d2;
  color: white;
}
</style>
