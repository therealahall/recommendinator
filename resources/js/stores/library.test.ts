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

  it('saveEdit updates item in list', async () => {
    const item = { db_id: 1, title: 'Book A', content_type: 'book', status: 'unread', rating: null, ignored: false }
    mockGet.mockResolvedValue([item])
    const store = useLibraryStore()
    await store.resetAndLoad()

    const updated = { ...item, status: 'completed', rating: 4 }
    mockPatch.mockResolvedValue(updated)
    store.editingItem = item as any
    await store.saveEdit(1, { status: 'completed', rating: 4 })

    expect(store.items[0].status).toBe('completed')
    expect(store.items[0].rating).toBe(4)
    expect(store.editingItem).toBeNull()
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
