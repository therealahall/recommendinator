import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'

const PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 250

export const useLibraryStore = defineStore('library', () => {
  const api = useApi()

  // State
  const items = ref<ContentItemResponse[]>([])
  const offset = ref(0)
  const hasMore = ref(true)
  const loading = ref(false)
  const error = ref('')

  // Filters
  const typeFilter = ref('')
  const statusFilter = ref('')
  const enrichmentFilter = ref('')
  const showIgnored = ref(false)

  // Search
  const searchQuery = ref('')
  const searchLoading = ref(false)
  const searchAnnouncement = ref('')
  let searchTimer: ReturnType<typeof setTimeout> | null = null
  // Tracks the in-flight load so a search triggered while a request is
  // already running awaits the real settle instead of an instantly-resolved
  // promise, keeping searchLoading bound to the true request lifecycle.
  let inFlightLoad: Promise<void> | null = null

  // Edit modal
  const editingItem = ref<ContentItemResponse | null>(null)
  const editSaving = ref(false)

  // Getters
  const totalLoaded = computed(() => items.value.length)

  // Actions
  function resetAndLoad() {
    offset.value = 0
    items.value = []
    hasMore.value = true
    error.value = ''
    return load(true)
  }

  function load(isReset = false): Promise<void> {
    // Never return undefined: a search triggered while a load is already in
    // flight must await that settle so its finally clears searchLoading.
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
      }
      if (typeFilter.value) params.type = typeFilter.value
      if (statusFilter.value) params.status = statusFilter.value
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
    key: 'type' | 'status' | 'enrichment' | 'showIgnored' | 'search',
    value: string | boolean,
  ) {
    if (key === 'search') {
      searchQuery.value = value as string
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
    return resetAndLoad()
  }

  async function openEdit(dbId: number) {
    const app = useAppStore()
    try {
      const item = await api.get<ContentItemResponse>(`/items/${dbId}`, {
        user_id: app.currentUserId,
      })
      editingItem.value = item
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load item'
    }
  }

  function closeEdit() {
    editingItem.value = null
    editSaving.value = false
  }

  async function saveEdit(dbId: number, data: ItemEditRequest) {
    const app = useAppStore()
    editSaving.value = true
    try {
      const updated = await api.patch<ContentItemResponse>(
        `/items/${dbId}`,
        { ...data, user_id: app.currentUserId },
      )

      // Update item in list
      const index = items.value.findIndex((i) => i.db_id === dbId)
      if (index >= 0) {
        items.value[index] = { ...items.value[index], ...updated }
      }

      closeEdit()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to save'
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

      // Update item in list
      const index = items.value.findIndex((i) => i.db_id === dbId)
      if (index >= 0) {
        items.value[index] = { ...items.value[index], ignored }
      }
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to update'
    }
  }

  function exportLibrary(format: 'csv' | 'json') {
    const app = useAppStore()
    if (!typeFilter.value) return

    const params = new URLSearchParams({
      type: typeFilter.value,
      format,
      user_id: app.currentUserId.toString(),
    })

    window.location.href = `/api/items/export?${params}`
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
    searchQuery,
    searchLoading,
    searchAnnouncement,
    editingItem,
    editSaving,
    totalLoaded,
    resetAndLoad,
    load,
    loadMore,
    cleanup,
    setFilter,
    openEdit,
    closeEdit,
    saveEdit,
    toggleIgnore,
    exportLibrary,
  }
})
