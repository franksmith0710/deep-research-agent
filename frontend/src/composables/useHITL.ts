import { ref } from 'vue'
import type { HITLEvent } from '../types'
import { submitHITL } from '../api'

let sseActive = false
let pendingSubmit = false

export function useHITL() {
  const showDialog = ref(false)
  const currentHITL = ref<HITLEvent | null>(null)

  function setSSEActive(active: boolean) {
    sseActive = active
  }

  function show(ev: HITLEvent) {
    if (!sseActive) return
    if (pendingSubmit) return
    currentHITL.value = ev
    showDialog.value = true
  }

  function close() {
    showDialog.value = false
    currentHITL.value = null
  }

  async function submit(data: Record<string, unknown>) {
    const hitl = currentHITL.value
    if (!hitl || pendingSubmit) return
    pendingSubmit = true
    try {
      await submitHITL(hitl.session_id, hitl.mode, data)
    } finally {
      close()
      pendingSubmit = false
    }
  }

  return { showDialog, currentHITL, show, close, submit, setSSEActive }
}
