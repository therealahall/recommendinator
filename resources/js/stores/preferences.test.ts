import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePreferencesStore, DEFAULT_WEIGHTS } from './preferences'

const mockGet = vi.fn()
const mockPut = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: vi.fn(),
    raw: vi.fn(),
  }),
}))

describe('usePreferencesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
  })

  it('has correct initial state', () => {
    const store = usePreferencesStore()
    expect(store.scorerWeights).toEqual({})
    expect(store.seriesInOrder).toBe(true)
    expect(store.varietyAfterCompletion).toBe(false)
    expect(store.customRules).toEqual([])
    expect(store.loading).toBe(false)
    expect(store.saveStatus).toBe('idle')
  })

  it('load populates state from API', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: { genre_match: 3.0, tag_overlap: 0.5 },
      series_in_order: false,
      variety_after_completion: true,
      content_length_preferences: { book: 'short' },
      custom_rules: ['avoid horror'],
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.scorerWeights).toEqual({ genre_match: 3.0, tag_overlap: 0.5 })
    expect(store.seriesInOrder).toBe(false)
    expect(store.varietyAfterCompletion).toBe(true)
    expect(store.contentLengthPreferences).toEqual({ book: 'short' })
    expect(store.customRules).toEqual(['avoid horror'])
  })

  it('load uses defaults on error', async () => {
    mockGet.mockRejectedValue(new Error('Network error'))

    const store = usePreferencesStore()
    await store.load()

    expect(store.scorerWeights).toEqual({})
    expect(store.seriesInOrder).toBe(true)
    expect(store.customRules).toEqual([])
  })

  it('getWeight returns stored value or default', () => {
    const store = usePreferencesStore()
    store.scorerWeights = { genre_match: 4.0 }

    expect(store.getWeight('genre_match')).toBe(4.0)
    expect(store.getWeight('tag_overlap')).toBe(DEFAULT_WEIGHTS.tag_overlap)
  })

  it('setWeight updates scorer weight', () => {
    const store = usePreferencesStore()
    store.setWeight('genre_match', 3.5)
    expect(store.scorerWeights.genre_match).toBe(3.5)
  })

  it('addRule appends trimmed rule', () => {
    const store = usePreferencesStore()
    store.addRule('  avoid horror  ')
    expect(store.customRules).toEqual(['avoid horror'])
  })

  it('addRule ignores empty strings', () => {
    const store = usePreferencesStore()
    store.addRule('   ')
    expect(store.customRules).toEqual([])
  })

  it('removeRule removes by index', () => {
    const store = usePreferencesStore()
    store.customRules = ['rule1', 'rule2', 'rule3']
    store.removeRule(1)
    expect(store.customRules).toEqual(['rule1', 'rule3'])
  })

  it('save sends preferences to API', async () => {
    mockPut.mockResolvedValue({})

    const store = usePreferencesStore()
    store.scorerWeights = { genre_match: 3.0 }
    store.seriesInOrder = false
    store.customRules = ['prefer sci-fi']

    await store.save()

    expect(mockPut).toHaveBeenCalledWith(
      '/users/1/preferences',
      expect.objectContaining({
        scorer_weights: { genre_match: 3.0 },
        series_in_order: false,
        custom_rules: ['prefer sci-fi'],
      }),
    )
    expect(store.saveStatus).toBe('saved')
  })

  it('save sets error status on failure', async () => {
    mockPut.mockRejectedValue(new Error('Server error'))

    const store = usePreferencesStore()
    await store.save()

    expect(store.saveStatus).toBe('error')
    expect(store.saveError).toBe('Server error')
  })
})
