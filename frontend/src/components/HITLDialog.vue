<script setup lang="ts">
import { ref, computed } from 'vue'
import type { useHITL } from '../composables/useHITL'

const props = defineProps<{ hitl: ReturnType<typeof useHITL> }>()

const selectedOptions = ref<string[]>([])
const userSupplement = ref<string>('')

const submitting = ref(false)

const hitlMode = computed(() => props.hitl.currentHITL.value?.mode as string | undefined)

async function confirm() {
  if (submitting.value) return
  submitting.value = true
  try {
    let data: Record<string, unknown> = {}
    if (hitlMode.value === 'scope_select') {
      data = { selectedDimensions: selectedOptions.value, user_supplement: userSupplement.value }
    }
    resetInputs()
    await props.hitl.submit(data)
  } finally {
    submitting.value = false
  }
}

function resetInputs() {
  selectedOptions.value = []
  userSupplement.value = ''
}
</script>

<template>
  <div class="hitl-overlay" @click.self="props.hitl.close()">
    <div class="hitl-dialog">
      <div class="hitl-header">需要你补充信息</div>
      <div class="hitl-body">
        <template v-if="hitlMode === 'scope_select'">
          <p v-if="(props.hitl.currentHITL.value?.options?.details_to_add as string[] || []).length" class="context-hint">
            <strong>建议补充：</strong>
            <span v-for="(d, i) in (props.hitl.currentHITL.value?.options?.details_to_add as string[] || [])" :key="i">
              {{ d }}<span v-if="i < ((props.hitl.currentHITL.value?.options?.details_to_add as string[] || []).length - 1)">、</span>
            </span>
          </p>

          <p class="options-title">请选择感兴趣的调研维度（可多选）：</p>
          <div class="options">
            <label v-for="dim in (props.hitl.currentHITL.value?.options?.dimensions as string[] || [])" :key="dim">
              <input type="checkbox" :value="dim" v-model="selectedOptions" /> {{ dim }}
            </label>
          </div>

          <p class="supplement-title">其他补充：</p>
          <textarea
            v-model="userSupplement"
            class="hitl-textarea"
            placeholder="可选，输入你想补充的其他内容..."
            rows="2"
          ></textarea>
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
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  animation: fadeIn 150ms ease;
}
.hitl-dialog {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  padding: 24px;
  min-width: 400px;
  max-width: 600px;
  color: var(--color-text-primary);
  box-shadow: var(--shadow-lg);
  animation: scaleIn 200ms ease;
}
.hitl-header { font-size: 16px; font-weight: 600; margin-bottom: 16px; }
.hitl-body { margin-bottom: 20px; line-height: 1.6; }
.context-query { margin-bottom: 8px; font-size: 14px; color: var(--color-text-secondary); }
.context-hint { margin-bottom: 12px; font-size: 13px; color: var(--color-text-secondary); }
.options-title { margin: 12px 0 6px; font-size: 14px; font-weight: 500; }
.supplement-title { margin: 14px 0 6px; font-size: 14px; font-weight: 500; }
.hitl-textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
  transition: border-color var(--transition-fast);
}
.hitl-textarea:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
}
.hitl-body .options label {
  display: block;
  padding: 6px 0;
  cursor: pointer;
}
.hitl-body .options input { margin-right: 8px; }
.hitl-footer { text-align: right; display: flex; gap: 8px; justify-content: flex-end; }
.hitl-footer button {
  padding: 8px 24px;
  border: none;
  border-radius: var(--radius-xs);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  transition: opacity var(--transition-fast);
}
.hitl-footer .cancel {
  background: var(--color-assistant-bg);
  color: var(--color-text-primary);
}
.hitl-footer .confirm {
  background: var(--color-primary);
  color: white;
}
.hitl-footer .confirm:hover:not(:disabled) { opacity: 0.9; }
.hitl-footer .confirm:disabled { opacity: 0.5; cursor: not-allowed; }

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}
</style>
