import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { DEFAULT_SORT, MAX_SEARCH_LENGTH } from '@/constants/library'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'

const PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 250

export const useLibraryStore = defineStore('library', () => {
  const api = useApi()

  const items = ref<ContentItemResponse[]>([])
  const offset = ref(0)
  const hasMore = ref(true)
  const loading = ref(false)
  const error = ref('')

  const typeFilter = ref('')
  const statusFilter = ref('')
  const enrichmentFilter = ref('')
  const showIgnored = ref(false)
  const needsRating = ref(false)
  const sortBy = ref(DEFAULT_SORT)

  const searchQuery = ref('')
  const searchLoading = ref(false)
  const searchAnnouncement = ref('')
  let searchTimer: ReturnType<typeof setTimeout> | null = null
  // A search started mid-load awaits the real settle, not a resolved promise.
  let inFlightLoad: Promise<void> | null = null

  // A refused save is the dialog's own, not the page's: the page banner sits
  // behind the overlay and says "Failed to load library" over it.
  const editingItem = ref<ContentItemResponse | null>(null)
  const editSaving = ref(false)
  const editError = ref('')

  const totalLoaded = computed(() => items.value.length)

  function resetAndLoad() {
    offset.value = 0
    items.value = []
    hasMore.value = true
    error.value = ''
    return load(true)
  }

  function load(isReset = false): Promise<void> {
    // Never undefined: the caller's finally clears searchLoading.
    if (loading.value) return inFlightLoad ?? Promise.resolve()
    inFlightLoad = runLoad(isReset).finally(() => {
      inFlightLoad = null
    })
    return inFlightLoad
  }

  async function runLoad(isReset: boolean): Promise<void> {
    const app = useAppStore()
    loading.value = true
    error.value = ''

    try {
      const params: Record<string, string | number | boolean> = {
        user_id: app.currentUserId,
        limit: PAGE_SIZE,
        offset: offset.value,
        sort_by: sortBy.value,
      }
      if (typeFilter.value) params.type = typeFilter.value
      if (needsRating.value) {
        // needs-rating forces completed, so a status param would contradict it.
        params.needs_rating = true
      } else if (statusFilter.value) {
        params.status = statusFilter.value
      }
      if (enrichmentFilter.value) params.enrichment = enrichmentFilter.value
      if (showIgnored.value) params.include_ignored = true
      if (searchQuery.value) params.search = searchQuery.value

      const result = await api.get<ContentItemResponse[]>('/items', params)

      if (result.length < PAGE_SIZE) {
        hasMore.value = false
      }

      if (isReset) {
        items.value = result
      } else {
        items.value = [...items.value, ...result]
      }

      offset.value += result.length

      if (isReset) announceSearch(result.length)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load library'
    } finally {
      loading.value = false
    }
  }

  function announceSearch(count: number) {
    const query = searchQuery.value
    if (!query) {
      searchAnnouncement.value = ''
    } else if (count === 0) {
      searchAnnouncement.value = `No items match “${query}”`
    } else if (count === 1) {
      searchAnnouncement.value = `1 item matches “${query}”`
    } else {
      searchAnnouncement.value = `${count} items match “${query}”`
    }
  }

  async function runSearch(): Promise<void> {
    searchLoading.value = true
    try {
      await resetAndLoad()
    } finally {
      searchLoading.value = false
    }
  }

  function cleanup() {
    if (searchTimer) {
      clearTimeout(searchTimer)
      searchTimer = null
    }
  }

  function loadMore() {
    if (!loading.value && hasMore.value) {
      return load(false)
    }
  }

  function setFilter(
    key: 'type' | 'status' | 'enrichment' | 'showIgnored' | 'search' | 'needsRating' | 'sort',
    value: string | boolean,
  ) {
    if (key === 'search') {
      // Clamped so a programmatic caller cannot send the API a 422.
      searchQuery.value = (value as string).slice(0, MAX_SEARCH_LENGTH)
      if (searchTimer) clearTimeout(searchTimer)
      searchTimer = setTimeout(() => {
        searchTimer = null
        runSearch()
      }, SEARCH_DEBOUNCE_MS)
      return
    }
    if (key === 'type') typeFilter.value = value as string
    else if (key === 'status') statusFilter.value = value as string
    else if (key === 'enrichment') enrichmentFilter.value = value as string
    else if (key === 'showIgnored') showIgnored.value = value as boolean
    else if (key === 'needsRating') needsRating.value = value as boolean
    else if (key === 'sort') sortBy.value = value as string
    return resetAndLoad()
  }

  /** One reload, not one per field: setFilter reloads on every call, so
   *  clearing five of them in a row would fire five requests. */
  function clearFilters() {
    typeFilter.value = ''
    statusFilter.value = ''
    enrichmentFilter.value = ''
    needsRating.value = false
    return resetAndLoad()
  }

  function syncRow(dbId: number, fields: Partial<ContentItemResponse>) {
    const index = items.value.findIndex((i) => i.db_id === dbId)
    if (index >= 0) items.value[index] = { ...items.value[index], ...fields }
  }

  async function openEdit(dbId: number) {
    const app = useAppStore()
    try {
      const item = await api.get<ContentItemResponse>(`/items/${dbId}`, {
        user_id: app.currentUserId,
      })
      editingItem.value = item
      syncRow(dbId, item)
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load item'
    }
  }

  function closeEdit() {
    editingItem.value = null
    editSaving.value = false
    editError.value = ''
  }

  async function saveEdit(dbId: number, data: ItemEditRequest) {
    const app = useAppStore()
    editSaving.value = true
    editError.value = ''
    try {
      const updated = await api.patch<ContentItemResponse>(
        `/items/${dbId}`,
        { ...data, user_id: app.currentUserId },
      )

      syncRow(dbId, updated)
      closeEdit()
    } catch (err) {
      editError.value = err instanceof Error ? err.message : 'Failed to save'
      editSaving.value = false
      throw err
    }
  }

  async function toggleIgnore(dbId: number, ignored: boolean) {
    const app = useAppStore()
    try {
      await api.patch(`/items/${dbId}/ignore`, {
        ignored,
        user_id: app.currentUserId,
      })

      syncRow(dbId, { ignored })
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to update'
    }
  }

  // One content type, or the whole library; no other filter narrows it.
  function exportUrl(format: 'csv' | 'json'): string {
    const app = useAppStore()
    const params = new URLSearchParams({
      format,
      user_id: app.currentUserId.toString(),
    })
    if (typeFilter.value) params.set('type', typeFilter.value)

    return `/api/items/export?${params}`
  }

  function exportLibrary(format: 'csv' | 'json') {
    window.location.href = exportUrl(format)
  }

  return {
    items,
    offset,
    hasMore,
    loading,
    error,
    typeFilter,
    statusFilter,
    enrichmentFilter,
    showIgnored,
    needsRating,
    sortBy,
    searchQuery,
    searchLoading,
    searchAnnouncement,
    editingItem,
    editSaving,
    editError,
    totalLoaded,
    resetAndLoad,
    load,
    loadMore,
    cleanup,
    setFilter,
    clearFilters,
    openEdit,
    closeEdit,
    saveEdit,
    toggleIgnore,
    exportUrl,
    exportLibrary,
  }
})
