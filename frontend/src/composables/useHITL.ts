import { ref } from 'vue'
import type { HITLEvent } from '../types'
import { submitHITL } from '../api'

let sseActive = false

export function useHITL() {
  const showDialog = ref(false)
  const currentHITL = ref<HITLEvent | null>(null)

  function setSSEActive(active: boolean) {
    sseActive = active
  }

  function show(ev: HITLEvent) {
    if (!sseActive) return
    currentHITL.value = ev
    showDialog.value = true
  }

  function close() {
    showDialog.value = false
    currentHITL.value = null
  }

  async function submit(data: Record<string, unknown>) {
    const hitl = currentHITL.value
    if (!hitl) return
    try {
      await submitHITL(hitl.session_id, hitl.mode, data)
    } finally {
      close()
    }
  }

  return { showDialog, currentHITL, show, close, submit, setSSEActive }
}
