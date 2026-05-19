import { ref } from 'vue'
import type { Ref } from 'vue'
import type { ChainEvent } from '../types'
import type { HITLEvent } from '../types'
import { useChatStore } from '../stores/chatStore'
import { useReportStore } from '../stores/reportStore'

interface HITLInstance {
  show: (ev: HITLEvent) => void
}

export function useSSE(hitl?: Ref<HITLInstance | null> | HITLInstance) {
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
        chatStore.appendStream(data.content as string || '')
        break
      case 'patch':
        reportStore.patchSection(
          data.section as string,
          data.content as string,
          data.append as boolean,
        )
        break
      case 'hitl': {
        const ev = data as unknown as HITLEvent
        if (hitl) {
          const h = 'value' in hitl ? hitl.value : hitl
          h?.show(ev)
        }
        break
      }
    }
  }

  return { connected, handleEvent }
}
