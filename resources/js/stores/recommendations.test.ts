import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRecommendationsStore } from './recommendations'
import type { ContentItemResponse } from '@/types/api'

function makeItem(overrides: Partial<ContentItemResponse> = {}): ContentItemResponse {
  return {
    id: 'test-1',
    db_id: 1,
    title: 'A',
    author: 'Author',
    content_type: 'book',
    status: 'unread',
    rating: null,
    review: null,
    source: 'goodreads',
    ignored: false,
    seasons_watched: null,
    total_seasons: null,
    enriched: true,
    genres: [],
    tags: [],
    description: null,
    ...overrides,
  }
}

const mockGet = vi.fn()
const mockPatch = vi.fn()
const mockRaw = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: vi.fn(),
    raw: (...args: unknown[]) => mockRaw(...args),
  }),
}))

vi.mock('@/composables/useSse', () => ({
  readSseStream: vi.fn(),
}))

describe('useRecommendationsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPatch.mockReset()
    mockRaw.mockReset()
  })

  it('has correct initial state', () => {
    const store = useRecommendationsStore()
    expect(store.items).toEqual([])
    expect(store.loading).toBe(false)
    expect(store.streaming).toBe(false)
    expect(store.error).toBe('')
    expect(store.contentType).toBe('book')
    expect(store.count).toBe(5)
    expect(store.editingItem).toBeNull()
    expect(store.editSaving).toBe(false)
  })

  it('fetch loads recommendations without LLM', async () => {
    const recs = [
      { db_id: 1, title: 'Rec 1', score: 0.9, similarity_score: 0.8, preference_score: 0.7, reasoning: 'test', score_breakdown: {} },
      { db_id: 2, title: 'Rec 2', score: 0.8, similarity_score: 0.7, preference_score: 0.6, reasoning: 'test2', score_breakdown: {} },
    ]
    mockGet.mockResolvedValue(recs)

    const store = useRecommendationsStore()
    await store.fetch(false)

    expect(store.items).toEqual(recs)
    expect(store.loading).toBe(false)
    expect(mockGet).toHaveBeenCalledWith('/recommendations', expect.objectContaining({
      type: 'book',
      count: 5,
      use_llm: false,
    }))
  })

  it('fetch sets error on failure', async () => {
    mockGet.mockRejectedValue(new Error('Server error'))

    const store = useRecommendationsStore()
    await store.fetch(false)

    expect(store.error).toBe('Server error')
    expect(store.loading).toBe(false)
  })

  it('ignoreItem removes from list', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
    ])
    mockPatch.mockResolvedValue({})

    const store = useRecommendationsStore()
    await store.fetch(false)
    await store.ignoreItem(1)

    expect(store.items.length).toBe(1)
    expect(store.items[0].db_id).toBe(2)
  })

  it('openEdit fetches the full item and stores it', async () => {
    const fullItem = makeItem()
    mockGet.mockResolvedValue(fullItem)

    const store = useRecommendationsStore()
    await store.openEdit(1)

    expect(mockGet).toHaveBeenCalledWith('/items/1', expect.objectContaining({ user_id: expect.anything() }))
    expect(store.editingItem).toEqual(fullItem)
  })

  it('closeEdit clears editing state', async () => {
    const fullItem = makeItem()
    mockGet.mockResolvedValue(fullItem)

    const store = useRecommendationsStore()
    await store.openEdit(1)
    store.closeEdit()

    expect(store.editingItem).toBeNull()
    expect(store.editSaving).toBe(false)
  })

  it('markComplete PATCHes /items/{dbId} and removes the card on success', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch(false)

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

  it('markComplete clears a stale error on a successful save', async () => {
    // A prior failed openEdit leaves an error in the bar. A subsequent successful
    // save must dismiss it (markComplete clears error.value before the PATCH), so
    // the user is not left looking at a stale failure message after success.
    const store = useRecommendationsStore()

    // Fail an openEdit to leave a stale error in the bar, then save directly with
    // no intervening fetch — so the cleared error can only come from markComplete
    // itself (which resets error.value on entry), not from fetch's own reset.
    mockGet.mockRejectedValueOnce(new Error('Not found'))
    await store.openEdit(99)
    expect(store.error).toBe('Not found')

    mockPatch.mockResolvedValue({})
    await store.markComplete(1, { status: 'completed', rating: null, review: null })

    expect(store.error).toBe('')
  })

  it('markComplete surfaces the error, resets editSaving, and re-throws on API error', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch(false)

    mockPatch.mockRejectedValue(new Error('Server error'))
    await expect(store.markComplete(1, { status: 'completed', rating: null, review: null })).rejects.toThrow('Server error')

    expect(store.editSaving).toBe(false)
    expect(store.error).toBe('Server error')
  })

  it('markComplete leaves the list unchanged when the saved item is not in the list', async () => {
    // markComplete still issues the PATCH for any dbId — there is no list guard.
    // When the saved item happens not to be in items (cannot occur via the UI),
    // the filter is a no-op and the list is untouched, but the PATCH still fires.
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch(false)

    mockPatch.mockResolvedValue({})
    await store.markComplete(99, { status: 'completed', rating: null, review: null })

    expect(mockPatch).toHaveBeenCalledWith('/items/99', expect.objectContaining({ status: 'completed' }))
    expect(store.items.map((i) => i.db_id)).toEqual([1, 2])
  })

  it('markComplete leaves the list unchanged on API error', async () => {
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
      { db_id: 2, title: 'B', score: 0.8, similarity_score: 0, preference_score: 0, reasoning: '', score_breakdown: {} },
    ])
    const store = useRecommendationsStore()
    await store.fetch(false)

    mockPatch.mockRejectedValue(new Error('Server error'))
    await expect(store.markComplete(1, { status: 'completed', rating: null, review: null })).rejects.toThrow('Server error')

    expect(store.items.length).toBe(2)
  })

  it('keeps the modal open and re-enables Save when the save fails (retry path)', async () => {
    // Locks in that a failed PATCH does not strand the modal: editSaving must
    // reset to false (so the Save button re-enables) and editingItem must stay
    // populated so the user can correct and retry without re-fetching.
    const fullItem = makeItem()
    mockGet.mockResolvedValue(fullItem)
    const store = useRecommendationsStore()
    await store.openEdit(1)

    mockPatch.mockRejectedValue(new Error('Server error'))
    await expect(store.markComplete(1, { status: 'completed', rating: null, review: null })).rejects.toThrow('Server error')

    expect(store.editSaving).toBe(false)
    expect(store.editingItem).toEqual(fullItem)
  })

  it('does not bleed state when opening edit on a second item after closing the first', async () => {
    // Open item 1, close, open item 2 — editingItem must reflect item 2 only.
    const itemA = makeItem({ db_id: 1, title: 'A', content_type: 'book', status: 'unread', rating: 3 })
    const itemB = makeItem({ db_id: 2, title: 'B', content_type: 'movie', status: 'completed', rating: null })
    const store = useRecommendationsStore()

    mockGet.mockResolvedValueOnce(itemA)
    await store.openEdit(1)
    expect(store.editingItem).toEqual(itemA)

    store.closeEdit()
    expect(store.editingItem).toBeNull()

    mockGet.mockResolvedValueOnce(itemB)
    await store.openEdit(2)
    expect(store.editingItem).toEqual(itemB)
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
