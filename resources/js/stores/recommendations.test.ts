import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRecommendationsStore } from './recommendations'

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
    expect(store.contentType).toBe('book')
    expect(store.count).toBe(5)
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
})
