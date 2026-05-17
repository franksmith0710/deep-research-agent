import { ref } from 'vue'
import type { HITLEvent } from '../types'

export function useHITL() {
  const showDialog = ref(false)
  const currentHITL = ref<HITLEvent | null>(null)

  function show(ev: HITLEvent) {
    currentHITL.value = ev
    showDialog.value = true
  }

  function close() {
    showDialog.value = false
    currentHITL.value = null
  }

  return { showDialog, currentHITL, show, close }
}
