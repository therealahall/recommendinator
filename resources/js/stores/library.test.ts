import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useLibraryStore } from './library'

const mockGet = vi.fn()
const mockPatch = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: vi.fn(),
    raw: vi.fn(),
  }),
}))

describe('useLibraryStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPatch.mockReset()
  })

  it('has correct initial state', () => {
    const store = useLibraryStore()
    expect(store.items).toEqual([])
    expect(store.hasMore).toBe(true)
    expect(store.loading).toBe(false)
    expect(store.typeFilter).toBe('')
    expect(store.statusFilter).toBe('')
    expect(store.needsRating).toBe(false)
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

  it('setFilter resets and reloads', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('type', 'movie')
    expect(store.typeFilter).toBe('movie')
    expect(mockGet).toHaveBeenCalled()
  })

  it('setFilter stores the enrichment filter and sends it as a query param', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('enrichment', 'not_enriched')

    expect(store.enrichmentFilter).toBe('not_enriched')
    const params = mockGet.mock.lastCall![1]
    expect(params.enrichment).toBe('not_enriched')
  })

  it('omits the enrichment param when no enrichment filter is set', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.resetAndLoad()

    const params = mockGet.mock.lastCall![1]
    expect(params.enrichment).toBeUndefined()
  })

  it('has empty search state initially', () => {
    const store = useLibraryStore()
    expect(store.searchQuery).toBe('')
    expect(store.searchLoading).toBe(false)
    expect(store.searchAnnouncement).toBe('')
  })

  it('setFilter search passes the search param and resets pagination', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([
        { db_id: 1, title: 'Dune', content_type: 'book', status: 'unread', ignored: false },
      ])
      const store = useLibraryStore()
      store.setFilter('search', 'dune')
      expect(store.searchQuery).toBe('dune')

      await vi.runAllTimersAsync()

      expect(store.offset).toBe(1)
      const params = mockGet.mock.calls[mockGet.mock.calls.length - 1][1]
      expect(params.search).toBe('dune')
      expect(params.offset).toBe(0)
    } finally {
      vi.useRealTimers()
    }
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

  it('toggles searchLoading around a search request', async () => {
    vi.useFakeTimers()
    try {
      let resolve: (v: unknown) => void = () => {}
      mockGet.mockReturnValue(new Promise((r) => { resolve = r }))
      const store = useLibraryStore()

      store.setFilter('search', 'dune')
      expect(store.searchLoading).toBe(false)

      await vi.advanceTimersByTimeAsync(250)
      expect(store.searchLoading).toBe(true)

      resolve([])
      await vi.runAllTimersAsync()
      expect(store.searchLoading).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('announces plural results for an active query', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([
        { db_id: 1, title: 'A', content_type: 'book', status: 'unread', ignored: false },
        { db_id: 2, title: 'B', content_type: 'book', status: 'unread', ignored: false },
      ])
      const store = useLibraryStore()
      store.setFilter('search', 'a')
      await vi.runAllTimersAsync()
      expect(store.searchAnnouncement).toBe('2 items match “a”')
    } finally {
      vi.useRealTimers()
    }
  })

  it('announces a singular result for an active query', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([
        { db_id: 1, title: 'A', content_type: 'book', status: 'unread', ignored: false },
      ])
      const store = useLibraryStore()
      store.setFilter('search', 'a')
      await vi.runAllTimersAsync()
      expect(store.searchAnnouncement).toBe('1 item matches “a”')
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

  it('clearing the search resets the query and announcement', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([])
      const store = useLibraryStore()
      store.setFilter('search', 'zzz')
      await vi.runAllTimersAsync()
      expect(store.searchAnnouncement).toBe('No items match “zzz”')

      store.setFilter('search', '')
      await vi.runAllTimersAsync()
      expect(store.searchQuery).toBe('')
      expect(store.searchAnnouncement).toBe('')
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

  it('cleanup cancels a pending debounced search', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([])
      const store = useLibraryStore()

      store.setFilter('search', 'dune')
      store.cleanup()
      await vi.runAllTimersAsync()

      expect(mockGet).not.toHaveBeenCalled()
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

  it('refreshes the announcement when a non-search filter changes during an active search', async () => {
    vi.useFakeTimers()
    try {
      mockGet.mockResolvedValue([
        { db_id: 1, title: 'A', content_type: 'book', status: 'unread', ignored: false },
        { db_id: 2, title: 'B', content_type: 'book', status: 'unread', ignored: false },
      ])
      const store = useLibraryStore()
      store.setFilter('search', 'a')
      await vi.runAllTimersAsync()
      expect(store.searchAnnouncement).toBe('2 items match “a”')

      mockGet.mockResolvedValue([
        { db_id: 1, title: 'A', content_type: 'book', status: 'unread', ignored: false },
      ])
      await store.setFilter('type', 'book')
      expect(store.searchAnnouncement).toBe('1 item matches “a”')
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

  it('needsRating composes with the type filter and still omits status', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('type', 'book')
    await store.setFilter('needsRating', true)

    const params = mockGet.mock.lastCall![1] as Record<string, unknown>
    expect(params.type).toBe('book')
    expect(params.needs_rating).toBe(true)
    expect(params.status).toBeUndefined()
  })

  it('needsRating composes with showIgnored and still omits status', async () => {
    mockGet.mockResolvedValue([])
    const store = useLibraryStore()

    await store.setFilter('showIgnored', true)
    await store.setFilter('needsRating', true)

    const params = mockGet.mock.lastCall![1] as Record<string, unknown>
    expect(params.needs_rating).toBe(true)
    expect(params.include_ignored).toBe(true)
    expect(params.status).toBeUndefined()
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

  it('toggleIgnore updates item in list', async () => {
    const item = { db_id: 5, title: 'Game', content_type: 'video_game', status: 'unread', ignored: false }
    mockGet.mockResolvedValue([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    mockPatch.mockResolvedValue({})
    await store.toggleIgnore(5, true)

    expect(store.items[0].ignored).toBe(true)
  })
})
