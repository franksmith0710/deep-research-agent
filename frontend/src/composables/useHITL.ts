import { ref } from 'vue'
import type { HITLEvent } from '../types'
import { researchHITL_SSE } from '../api'
import { useChatStore } from '../stores/chatStore'

export function useHITL() {
  const showDialog = ref(false)
  const currentHITL = ref<HITLEvent | null>(null)
  let submitting = false
  let currentSource: ReturnType<typeof researchHITL_SSE> | null = null

  function restore(ev: HITLEvent) {
    if (submitting) return
    currentHITL.value = ev
    showDialog.value = true
  }

  function close() {
    showDialog.value = false
    currentHITL.value = null
  }

  function abort() {
    currentSource?.abort()
    currentSource = null
  }

  async function submit(data: Record<string, unknown>) {
    const hitl = currentHITL.value
    if (!hitl || submitting) return
    submitting = true
    try {
      const chatStore = useChatStore()
      currentSource = researchHITL_SSE(
        hitl.session_id,
        hitl.mode,
        data,
        (event, dataStr) => {
          let d: Record<string, unknown>
          try { d = JSON.parse(dataStr) } catch { return }
          switch (event) {
            case 'chain':
              chatStore.addChainEvent(d as any)
              break
            case 'text':
              chatStore.appendStream((d.content as string) || '')
              break
          }
        },
        (err) => {
          chatStore.addChainEvent({ type: 'action_result', node: 'error', content: err, ts: '' })
        },
        () => {
          chatStore.finalizeStream()
          chatStore.runStatus = 'completed'
        },
      )
    } finally {
      close()
      submitting = false
    }
  }

  return { showDialog, currentHITL, restore, close, submit, abort }
}
