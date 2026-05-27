import { ref } from 'vue'
import type { Ref } from 'vue'
import type { ChainEvent } from '../types'
import { useChatStore } from '../stores/chatStore'

export function useSSE() {
  const connected = ref(false)
  const chatStore = useChatStore()

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
    }
  }

  return { connected, handleEvent }
}
