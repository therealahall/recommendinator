import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ContentItemResponse, ItemEditRequest, RecommendationResponse } from '@/types/api'

export const useRecommendationsStore = defineStore('recommendations', () => {
  const api = useApi()

  // State
  const items = ref<RecommendationResponse[]>([])
  const loading = ref(false)
  const error = ref('')
  const contentType = ref('book')
  const count = ref(5)

  // Edit modal (reused from the library to mark recommendations complete). A
  // refused save is the dialog's own: the page banner sits behind the overlay.
  const editingItem = ref<ContentItemResponse | null>(null)
  const editSaving = ref(false)
  const editError = ref('')

  // Actions
  async function fetch() {
    const app = useAppStore()
    loading.value = true
    error.value = ''
    items.value = []

    try {
      const result = await api.get<RecommendationResponse[]>('/recommendations', {
        type: contentType.value,
        count: count.value,
        user_id: app.currentUserId,
      })
      items.value = result
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load recommendations'
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
    editError.value = ''
  }

  async function markComplete(dbId: number, data: ItemEditRequest) {
    const app = useAppStore()
    editSaving.value = true
    editError.value = ''
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
      editError.value = err instanceof Error ? err.message : 'Failed to save'
      editSaving.value = false
      throw err
    }
  }

  return {
    items,
    loading,
    error,
    contentType,
    count,
    editingItem,
    editSaving,
    editError,
    fetch,
    ignoreItem,
    openEdit,
    closeEdit,
    markComplete,
  }
})
