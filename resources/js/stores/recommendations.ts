import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ContentItemResponse, ItemEditRequest, RecommendationResponse } from '@/types/api'

export const useRecommendationsStore = defineStore('recommendations', () => {
  const api = useApi()

  const items = ref<RecommendationResponse[]>([])
  const loading = ref(false)
  const error = ref('')
  const contentType = ref('book')
  const count = ref(5)
  // Kept per item rather than dropping the row: the undo has to sit where the
  // card was, and a re-inserted row would come back at the wrong rank.
  const ignored = ref<Set<number>>(new Set())
  /** Tells "nothing yet" from "nothing matched"; fetch() empties before it asks. */
  const hasRun = ref(false)
  /** What the list on screen was ranked for; the selector scopes the NEXT run. */
  const ranType = ref('')
  watch(contentType, () => {
    hasRun.value = false
  })

  // Edit modal (reused from the library to mark recommendations complete). A
  // refused save is the dialog's own: the page banner sits behind the overlay.
  const editingItem = ref<ContentItemResponse | null>(null)
  const editSaving = ref(false)
  const editError = ref('')

  async function fetch() {
    const app = useAppStore()
    loading.value = true
    error.value = ''
    items.value = []
    ignored.value = new Set()

    try {
      const result = await api.get<RecommendationResponse[]>('/recommendations', {
        type: contentType.value,
        count: count.value,
        user_id: app.currentUserId,
      })
      items.value = result
      ranType.value = contentType.value
      hasRun.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load recommendations'
    } finally {
      loading.value = false
    }
  }

  // Rejects to the caller: ignoring is a one-click exclusion from every future
  // recommendation, and the page is what can offer the undo beside it.
  async function setIgnored(dbId: number, value: boolean) {
    const app = useAppStore()
    await api.patch(`/items/${dbId}/ignore`, {
      ignored: value,
      user_id: app.currentUserId,
    })
    const next = new Set(ignored.value)
    if (value) next.add(dbId)
    else next.delete(dbId)
    ignored.value = next
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
      items.value = items.value.filter((i) => i.db_id !== dbId)
      closeEdit()
    } catch (err) {
      // Leave the list unchanged and keep the modal open so the user can retry,
      // then re-throw so the page can react (it skips moving focus out of the
      // form).
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
    ignored,
    hasRun,
    ranType,
    editingItem,
    editSaving,
    editError,
    fetch,
    setIgnored,
    openEdit,
    closeEdit,
    markComplete,
  }
})
