import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useLibraryStore } from './library'
import { MAX_SEARCH_LENGTH } from '@/constants/library'

const mockGet = vi.fn()
const mockPatch = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: vi.fn(),
  }),
}))

describe('useLibraryStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPatch.mockReset()
  })

  it('resetAndLoad fetches items', async () => {
    const items = [
      { db_id: 1, title: 'Book A', content_type: 'book', status: 'completed', ignored: false },
      { db_id: 2, title: 'Book B', content_type: 'book', status: 'unread', ignored: false },
    ]
    mockGet.mockResolvedValue(items)

    const store = useLibraryStore()
    await store.resetAndLoad()

    expect(store.items).toEqual(items)
    expect(store.offset).toBe(2)
    expect(store.hasMore).toBe(false) // < PAGE_SIZE items
  })

  it('loadMore appends items', async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => ({
      db_id: i, title: `Item ${i}`, content_type: 'book', status: 'completed', ignored: false,
    }))
    const page2 = [{ db_id: 50, title: 'Item 50', content_type: 'book', status: 'unread', ignored: false }]

    mockGet.mockResolvedValueOnce(page1)
    const store = useLibraryStore()
    await store.resetAndLoad()
    expect(store.items.length).toBe(50)
    expect(store.hasMore).toBe(true)

    mockGet.mockResolvedValueOnce(page2)
    await store.loadMore()
    expect(store.items.length).toBe(51)
    expect(store.hasMore).toBe(false)
  })

  it('setFilter stores the enrichment filter and sends it as a query param', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('enrichment', 'not_enriched')

    expect(store.enrichmentFilter).toBe('not_enriched')
    const params = mockGet.mock.lastCall![1]
    expect(params.enrichment).toBe('not_enriched')
  })

  it('debounce coalesces rapid search changes into one request', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([])
      const store = useLibraryStore()

      store.setFilter('search', 'd')
      store.setFilter('search', 'du')
      store.setFilter('search', 'dune')

      expect(mockGet).not.toHaveBeenCalled()
      await vi.runAllTimersAsync()

      expect(mockGet).toHaveBeenCalledTimes(1)
      expect(mockGet.mock.calls[0][1].search).toBe('dune')
    } finally {
      vi.useRealTimers()
    }
  })

  it('announces no results for an active query', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([])
      const store = useLibraryStore()
      store.setFilter('search', 'zzz')
      await vi.runAllTimersAsync()
      expect(store.searchAnnouncement).toBe('No items match “zzz”')
    } finally {
      vi.useRealTimers()
    }
  })

  it('clamps an over-long search term to the length the API accepts', async () => {
    // Regression: the store sent whatever term it was handed, so anything over
    // MAX_SEARCH_LENGTH came back 422 and surfaced as a bare status line.
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([])
      const store = useLibraryStore()

      store.setFilter('search', 'a'.repeat(MAX_SEARCH_LENGTH + 1))
      expect(store.searchQuery).toBe('a'.repeat(MAX_SEARCH_LENGTH))

      await vi.runAllTimersAsync()

      const params = mockGet.mock.lastCall![1]
      expect(params.search).toBe('a'.repeat(MAX_SEARCH_LENGTH))
    } finally {
      vi.useRealTimers()
    }
  })

  it('clears searchLoading when a search is triggered while a load is in flight', async () => {
    vi.useFakeTimers()
    try {
      let resolveFirst: (v: unknown) => void = () => {}
      mockGet.mockReturnValueOnce(new Promise((r) => { resolveFirst = r }))
      const store = useLibraryStore()

      // Kick off a load that stays in flight.
      const firstLoad = store.resetAndLoad()
      expect(store.loading).toBe(true)

      // Type a search while that load is still running: the debounced
      // runSearch must await the real settle, not strand searchLoading.
      mockGet.mockResolvedValue([])
      store.setFilter('search', 'dune')
      await vi.advanceTimersByTimeAsync(250)
      expect(store.searchLoading).toBe(true)

      resolveFirst([])
      await firstLoad
      await vi.runAllTimersAsync()
      expect(store.searchLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('resets searchLoading and sets error when the search request rejects', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockRejectedValue(new Error('network down'))
      const store = useLibraryStore()

      store.setFilter('search', 'dune')
      await vi.runAllTimersAsync()

      expect(store.searchLoading).toBe(false)
      expect(store.error).toBe('network down')
    } finally {
      vi.useRealTimers()
    }
  })

  it('setFilter needsRating sends needs_rating, omits status, and leaves statusFilter untouched', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('needsRating', true)

    expect(store.needsRating).toBe(true)
    // statusFilter is independent — the toggle must not mutate it.
    expect(store.statusFilter).toBe('')
    const params = mockGet.mock.lastCall![1] as Record<string, unknown>
    expect(params.needs_rating).toBe(true)
    expect(params.status).toBeUndefined()
    expect(store.offset).toBe(0)
  })

  it('toggling needsRating off restores the user\'s prior status filter', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    // User picks a real status, then toggles needsRating on and back off.
    await store.setFilter('status', 'unread')
    await store.setFilter('needsRating', true)
    await store.setFilter('needsRating', false)

    expect(store.needsRating).toBe(false)
    // The orthogonal redesign means the prior status survives the round-trip.
    expect(store.statusFilter).toBe('unread')
    const params = mockGet.mock.lastCall![1] as Record<string, unknown>
    expect(params.needs_rating).toBeUndefined()
    expect(params.status).toBe('unread')
  })

  it('exports the whole library when no content type is selected', () => {
    // Regression: exportLibrary returned early without a type filter, so the
    // default view's Export menu picked a format and downloaded nothing.
    const store = useLibraryStore()

    const url = new URL(store.exportUrl('csv'), 'http://localhost')

    expect(url.pathname).toBe('/api/items/export')
    expect(url.searchParams.get('type')).toBeNull()
    expect(url.searchParams.get('format')).toBe('csv')
  })

  it('exports only the selected content type when one is filtered', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('type', 'movie')

    expect(new URL(store.exportUrl('json'), 'http://localhost').searchParams.get('type')).toBe('movie')
  })

  it('sends the chosen sort order as sort_by, on the next page as well', async () => {
    // A page fetched in a different order than the one before it repeats some
    // rows and drops others, so sort_by belongs on every request rather than
    // on the reset that follows the choice.
    const page = Array.from({ length: 50 }, (_, i) => ({
      db_id: i, title: `Item ${i}`, content_type: 'book', status: 'completed', ignored: false,
    }))
    mockGet.mockResolvedValueOnce(page).mockResolvedValueOnce([])
    const store = useLibraryStore()

    await store.setFilter('sort', 'rating')
    await store.loadMore()

    expect(store.sortBy).toBe('rating')
    expect(mockGet.mock.calls.map(call => call[1].sort_by)).toEqual(['rating', 'rating'])
    expect(mockGet.mock.lastCall![1].offset).toBe(50)
  })

  it('saveEdit updates item in list', async () => {
    const item = { db_id: 1, title: 'Book A', content_type: 'book', status: 'unread', rating: null, ignored: false }
    mockGet.mockResolvedValue([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    const updated = { ...item, status: 'completed', rating: 4 }
    mockPatch.mockResolvedValue(updated)
    store.editingItem = item as any
    await store.saveEdit(1, { status: 'completed', rating: 4 })

    expect(mockPatch.mock.lastCall![0]).toBe('/items/1')
    expect(store.items[0].status).toBe('completed')
    expect(store.items[0].rating).toBe(4)
    expect(store.editingItem).toBeNull()
  })

  it('saveEdit keeps a refused save on the dialog, not on the load banner', async () => {
    // The banner renders behind the modal overlay and prefixes "Failed to load
    // library:", so a refusal routed there was unreadable and misdescribed.
    const item = { db_id: 1, title: 'Book A', content_type: 'book', status: 'unread', rating: null, ignored: false }
    mockGet.mockResolvedValue([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    mockPatch.mockRejectedValue(new Error('Review must be at most 10000 characters.'))
    store.editingItem = item as any
    await expect(store.saveEdit(1, { status: 'unread', review: 'x' })).rejects.toThrow()

    expect(store.editError).toBe('Review must be at most 10000 characters.')
    expect(store.error).toBe('')
    expect(store.editingItem).not.toBeNull()
    expect(store.editSaving).toBe(false)
  })

  it('saveEdit posts enrichment fields and flips the local enriched flag', async () => {
    const item = { db_id: 1, title: 'Book A', content_type: 'book', status: 'unread', rating: null, ignored: false, enriched: false, genres: [], tags: [], description: null }
    mockGet.mockResolvedValue([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    const updated = { ...item, enriched: true, genres: ['Sci-Fi'], tags: ['classic'], description: 'A tale.' }
    mockPatch.mockResolvedValue(updated)
    await store.saveEdit(1, { status: 'unread', genres: ['Sci-Fi'], tags: ['classic'], description: 'A tale.' })

    const body = mockPatch.mock.lastCall![1]
    expect(body).toMatchObject({ genres: ['Sci-Fi'], tags: ['classic'], description: 'A tale.' })
    expect(store.items[0].enriched).toBe(true)
    expect(store.items[0].genres).toEqual(['Sci-Fi'])
  })

  it('openEdit refreshes the card behind the dialog', async () => {
    // Restoring automatic enrichment reopens the dialog on fresh data, and the
    // card behind it read enriched until a reload — the badge said the opposite.
    const item = { db_id: 1, title: 'Book A', content_type: 'book', status: 'unread', ignored: false, enriched: true, manually_enriched: true }
    mockGet.mockResolvedValueOnce([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    mockGet.mockResolvedValueOnce({ ...item, enriched: false, manually_enriched: false })
    await store.openEdit(1)

    expect(store.items[0].enriched).toBe(false)
    expect(store.items[0].manually_enriched).toBe(false)
  })
})
