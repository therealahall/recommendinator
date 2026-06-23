import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { readSseStream } from '@/composables/useSse'
import type { ContentItemResponse, ItemEditRequest, RecommendationResponse } from '@/types/api'

interface RecStreamEvent {
  type: 'recommendations' | 'blurb' | 'done' | 'error'
  items?: RecommendationResponse[]
  index?: number
  llm_reasoning?: string
  message?: string
}

export const useRecommendationsStore = defineStore('recommendations', () => {
  const api = useApi()

  // State
  const items = ref<RecommendationResponse[]>([])
  const loading = ref(false)
  const streaming = ref(false)
  const error = ref('')
  const contentType = ref('book')
  const count = ref(5)

  // Edit modal (reused from the library to mark recommendations complete)
  const editingItem = ref<ContentItemResponse | null>(null)
  const editSaving = ref(false)

  // Actions
  async function fetch(useLlm: boolean) {
    const app = useAppStore()
    loading.value = true
    error.value = ''
    items.value = []
    streaming.value = false

    // Use streaming when LLM reasoning is available
    if (useLlm && app.features.llm_reasoning_enabled) {
      await fetchStreaming()
      return
    }

    try {
      const result = await api.get<RecommendationResponse[]>('/recommendations', {
        type: contentType.value,
        count: count.value,
        use_llm: useLlm,
        user_id: app.currentUserId,
      })
      items.value = result
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load recommendations'
    } finally {
      loading.value = false
    }
  }

  async function fetchStreaming() {
    const app = useAppStore()
    streaming.value = true

    try {
      const response = await api.raw('/recommendations/stream', {
        params: {
          type: contentType.value,
          count: count.value,
          user_id: app.currentUserId,
        },
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      await readSseStream<RecStreamEvent>(response, (event) => {
        if (event.type === 'recommendations' && event.items) {
          items.value = event.items
          loading.value = false
        } else if (event.type === 'blurb' && typeof event.index === 'number') {
          if (event.index >= 0 && event.index < items.value.length) {
            items.value[event.index] = {
              ...items.value[event.index],
              llm_reasoning: event.llm_reasoning ?? null,
            }
          }
        } else if (event.type === 'done') {
          streaming.value = false
        } else if (event.type === 'error') {
          error.value = event.message || 'Unknown error'
          streaming.value = false
          loading.value = false
        }
      })
    } catch (err) {
      // Fallback to sync endpoint
      streaming.value = false
      try {
        const result = await api.get<RecommendationResponse[]>('/recommendations', {
          type: contentType.value,
          count: count.value,
          use_llm: true,
          user_id: app.currentUserId,
        })
        items.value = result
      } catch (fallbackErr) {
        error.value = fallbackErr instanceof Error ? fallbackErr.message : 'Failed to load recommendations'
      }
    } finally {
      loading.value = false
    }
  }

  async function ignoreItem(dbId: number) {
    const app = useAppStore()
    try {
      await api.patch(`/items/${dbId}/ignore`, {
        ignored: true,
        user_id: app.currentUserId,
      })
      // Remove from list
      items.value = items.value.filter((i) => i.db_id !== dbId)
    } catch {
      // Silently ignore
    }
  }

  async function openEdit(dbId: number) {
    const app = useAppStore()
    error.value = ''
    try {
      editingItem.value = await api.get<ContentItemResponse>(`/items/${dbId}`, {
        user_id: app.currentUserId,
      })
    } catch (err) {
      // The user clicked expecting a modal, so surface the failure instead of
      // leaving a dead button (mirrors the library store's openEdit).
      error.value = err instanceof Error ? err.message : 'Failed to load item'
    }
  }

  function closeEdit() {
    editingItem.value = null
    editSaving.value = false
  }

  async function markComplete(dbId: number, data: ItemEditRequest) {
    const app = useAppStore()
    editSaving.value = true
    error.value = ''
    try {
      await api.patch<ContentItemResponse>(`/items/${dbId}`, {
        ...data,
        user_id: app.currentUserId,
      })
      // Remove from list, mirroring ignore.
      items.value = items.value.filter((i) => i.db_id !== dbId)
      closeEdit()
    } catch (err) {
      // Surface the failure (mirrors the library store's saveEdit). Leave the
      // list unchanged and keep the modal open so the user can retry, then
      // re-throw so the page can react (it skips moving focus out of the form).
      error.value = err instanceof Error ? err.message : 'Failed to save'
      editSaving.value = false
      throw err
    }
  }

  return {
    items,
    loading,
    streaming,
    error,
    contentType,
    count,
    editingItem,
    editSaving,
    fetch,
    ignoreItem,
    openEdit,
    closeEdit,
    markComplete,
  }
})
