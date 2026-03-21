import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { readSseStream } from '@/composables/useSse'
import type { MemoryResponse, ProfileResponse, SseChunk } from '@/types/api'
import type { ChatMessage } from '@/types/chat'


const TOOL_LABELS: Record<string, string> = {
  mark_completed: 'Marking as completed',
  update_rating: 'Updating rating',
  add_to_wishlist: 'Adding to wishlist',
  save_memory: 'Saving preference',
  search_items: 'Searching items',
  clarify_item: 'Clarifying item',
}

export const useChatStore = defineStore('chat', () => {
  const api = useApi()

  // State
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const showWelcome = ref(true)
  const memories = ref<MemoryResponse[]>([])
  const profile = ref<ProfileResponse | null>(null)
  const profileRegenerating = ref(false)
  let nextMessageId = 1

  function pushMessage(message: Omit<ChatMessage, 'id'>): number {
    const id = nextMessageId++
    messages.value.push({ ...message, id })
    return id
  }

  // Actions
  async function send(text: string, contentType?: string) {
    if (!text.trim() || isStreaming.value) return
    const app = useAppStore()

    showWelcome.value = false
    pushMessage({ role: 'user', content: text })
    isStreaming.value = true

    let currentAssistantIdx = -1

    try {
      const body: Record<string, unknown> = {
        user_id: app.currentUserId,
        message: text,
      }
      if (contentType) body.content_type = contentType

      const response = await api.raw('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) throw new Error('Chat request failed')

      await readSseStream<SseChunk>(response, (chunk) => {
        if (chunk.type === 'text' && chunk.content) {
          if (currentAssistantIdx >= 0 && !messages.value[currentAssistantIdx].isToolIndicator) {
            messages.value[currentAssistantIdx].content += chunk.content
          } else {
            pushMessage({ role: 'assistant', content: chunk.content })
            currentAssistantIdx = messages.value.length - 1
          }
        } else if (chunk.type === 'tool_call') {
          const label = TOOL_LABELS[chunk.tool] || chunk.tool
          pushMessage({
            role: 'assistant',
            content: `${label}...`,
            isToolIndicator: true,
            toolName: chunk.tool,
          })
          currentAssistantIdx = messages.value.length - 1
        } else if (chunk.type === 'tool_result') {
          // Update the tool indicator
          const result = chunk.result as { success?: boolean; message?: string }
          const toolName = chunk.tool
          let idx = -1
          for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].isToolIndicator && messages.value[i].toolName === toolName) {
              idx = i
              break
            }
          }
          if (idx >= 0) {
            messages.value[idx].content = result.message || 'Done'
            messages.value[idx].toolSuccess = !!result.success
          }
          // Reset so next text creates new assistant message
          currentAssistantIdx = -1
          // Refresh memories if relevant
          const relevantTools = ['save_memory', 'mark_completed', 'update_rating']
          if (relevantTools.includes(toolName)) {
            loadMemories()
          }
        } else if (chunk.type === 'done') {
          isStreaming.value = false
        } else if (chunk.type === 'error') {
          isStreaming.value = false
          pushMessage({ role: 'assistant', content: `Error: ${chunk.content}` })
        }
      })
    } catch {
      pushMessage({ role: 'assistant', content: 'Sorry, I encountered an error. Please try again.' })
    } finally {
      isStreaming.value = false
    }
  }

  async function reset() {
    const app = useAppStore()
    try {
      await api.post(`/chat/reset`, { user_id: app.currentUserId })
      messages.value = []
      showWelcome.value = true
    } catch {
      // Ignore
    }
  }

  async function loadMemories() {
    const app = useAppStore()
    try {
      memories.value = await api.get<MemoryResponse[]>('/memories', {
        user_id: app.currentUserId,
        include_inactive: true,
      })
    } catch {
      memories.value = []
    }
  }

  async function addMemory(text: string) {
    const app = useAppStore()
    try {
      await api.post('/memories', { user_id: app.currentUserId, memory_text: text })
      await loadMemories()
    } catch {
      throw new Error('Failed to save memory')
    }
  }

  async function toggleMemory(id: number, currentlyActive: boolean) {
    try {
      await api.put(`/memories/${id}`, { is_active: !currentlyActive })
      await loadMemories()
    } catch {
      // Ignore
    }
  }

  async function deleteMemory(id: number) {
    try {
      await api.delete(`/memories/${id}`)
      await loadMemories()
    } catch {
      // Ignore
    }
  }

  async function loadProfile() {
    const app = useAppStore()
    try {
      profile.value = await api.get<ProfileResponse>('/profile', {
        user_id: app.currentUserId,
      })
    } catch {
      profile.value = null
    }
  }

  async function regenerateProfile() {
    const app = useAppStore()
    profileRegenerating.value = true
    try {
      profile.value = await api.post<ProfileResponse>('/profile/regenerate', {
        user_id: app.currentUserId,
      })
    } catch {
      // Ignore
    } finally {
      profileRegenerating.value = false
    }
  }

  return {
    messages,
    isStreaming,
    showWelcome,
    memories,
    profile,
    profileRegenerating,
    send,
    reset,
    loadMemories,
    addMemory,
    toggleMemory,
    deleteMemory,
    loadProfile,
    regenerateProfile,
  }
})
