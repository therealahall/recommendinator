import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import { useRecommendationsStore } from './recommendations'

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

describe('useRecommendationsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPatch.mockReset()
  })

  it('fetch loads recommendations', async () => {
    const recs = [
      { db_id: 1, title: 'Rec 1', score: 0.9, reasoning: 'test', score_breakdown: {} },
      { db_id: 2, title: 'Rec 2', score: 0.8, reasoning: 'test2', score_breakdown: {} },
    ]
    mockGet.mockResolvedValue(recs)

    const store = useRecommendationsStore()
    await store.fetch()

    expect(store.items).toEqual(recs)
    expect(store.loading).toBe(false)
    expect(mockGet).toHaveBeenCalledWith('/recommendations', expect.objectContaining({
      type: 'book',
      count: 5,
    }))
  })

  it('fetch sets error on failure', async () => {
    mockGet.mockRejectedValue(new Error('Server error'))

    const store = useRecommendationsStore()
    await store.fetch()

    expect(store.error).toBe('Server error')
    expect(store.loading).toBe(false)
  })

  it('setIgnored marks the row in place, so the undo can sit where the card was', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, reasoning: '', score_breakdown: {} },
    ])
    mockPatch.mockResolvedValue({})

    const store = useRecommendationsStore()
    await store.fetch()
    await store.setIgnored(1, true)

    expect(mockPatch).toHaveBeenCalledWith('/items/1/ignore', expect.objectContaining({
      ignored: true,
    }))
    expect(store.items.length).toBe(2)
    expect(store.ignored.has(1)).toBe(true)

    await store.setIgnored(1, false)
    expect(store.ignored.has(1)).toBe(false)
  })

  it('setIgnored rejects rather than leaving the button looking dead', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch()

    mockPatch.mockRejectedValue(new Error('Server error'))
    await expect(store.setIgnored(1, true)).rejects.toThrow('Server error')
    expect(store.ignored.has(1)).toBe(false)
  })

  it('records that a generate finished, so zero results is not read as "not run yet"', async () => {
    mockGet.mockResolvedValue([])
    const store = useRecommendationsStore()

    expect(store.hasRun).toBe(false)
    await store.fetch()
    expect(store.hasRun).toBe(true)
  })

  it('forgets the run when the type changes, so the copy names one that ran', async () => {
    mockGet.mockResolvedValue([])
    const store = useRecommendationsStore()
    await store.fetch()

    store.contentType = 'movie'
    await nextTick()

    expect(store.hasRun).toBe(false)
  })

  it('markComplete PATCHes /items/{dbId} and removes the card on success', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch()

    mockPatch.mockResolvedValue({})
    await store.markComplete(1, { status: 'completed', rating: 4, review: null })

    expect(mockPatch).toHaveBeenCalledWith('/items/1', expect.objectContaining({
      status: 'completed',
      rating: 4,
      review: null,
      user_id: expect.anything(),
    }))
    expect(store.items.length).toBe(1)
    expect(store.items[0].db_id).toBe(2)
    expect(store.editingItem).toBeNull()
    expect(store.editSaving).toBe(false)
  })

  it('markComplete surfaces the error, resets editSaving, and re-throws on API error', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch()

    mockPatch.mockRejectedValue(new Error('Server error'))
    await expect(store.markComplete(1, { status: 'completed', rating: null, review: null })).rejects.toThrow('Server error')

    expect(store.editSaving).toBe(false)
    // The dialog's own error, not the page's: the page banner renders behind
    // the overlay and calls every failure a failure to load recommendations.
    expect(store.editError).toBe('Server error')
    expect(store.error).toBe('')
  })

  it('openEdit surfaces an error and leaves editingItem null when the item GET fails', async () => {
    // The user clicked expecting a modal, so a failed detail fetch must not be a
    // silent dead button: editingItem stays null (no stale/partial modal) AND the
    // store error is set so the page can tell the user it failed.
    mockGet.mockRejectedValue(new Error('Not found'))
    const store = useRecommendationsStore()
    await store.openEdit(99)

    expect(store.editingItem).toBeNull()
    expect(store.error).toBe('Not found')
    // openEdit must not touch the save flag; only markComplete owns editSaving.
    expect(store.editSaving).toBe(false)
  })
})
