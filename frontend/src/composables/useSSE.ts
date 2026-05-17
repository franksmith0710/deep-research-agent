import { ref } from 'vue'
import type { ChainEvent } from '../types'
import { useChatStore } from '../stores/chatStore'
import { useReportStore } from '../stores/reportStore'

export function useSSE() {
  const connected = ref(false)
  const chatStore = useChatStore()
  const reportStore = useReportStore()

  function handleEvent(event: string, dataStr: string) {
    let data: Record<string, unknown>
    try {
      data = JSON.parse(dataStr)
    } catch {
      return
    }

    switch (event) {
      case 'chain': {
        const ev = data as unknown as ChainEvent
        chatStore.addChainEvent(ev)
        break
      }
      case 'text':
        chatStore.addMessage({
          id: Date.now(),
          role: 'assistant',
          content: data.content as string,
          created_at: new Date().toISOString(),
        })
        break
      case 'patch':
        reportStore.patchSection(
          data.section as string,
          data.content as string,
          data.append as boolean,
        )
        break
      case 'hitl':
        // HITL handled by useHITL
        break
    }
  }

  return { connected, handleEvent }
}
