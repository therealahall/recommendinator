import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePreferencesStore, DEFAULT_WEIGHTS } from './preferences'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockApplyTheme = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

vi.mock('@/stores/theme', () => ({
  useThemeStore: () => ({
    applyTheme: mockApplyTheme,
    currentThemeId: null,
    defaultThemeId: 'nord',
  }),
}))

const STORED_PREFS = {
  scorer_weights: {},
  series_in_order: true,
  variety_penalty: 0,
  content_length_preferences: {},
  custom_rules: [],
  theme: '',
}

/** A store in the only state the Save button is reachable from: loaded. */
async function loadedStore() {
  mockGet.mockResolvedValue(STORED_PREFS)
  const store = usePreferencesStore()
  await store.load()
  return store
}

describe('usePreferencesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockApplyTheme.mockReset()
  })

  it('has correct initial state', () => {
    const store = usePreferencesStore()
    expect(store.scorerWeights).toEqual({})
    expect(store.seriesInOrder).toBe(true)
    expect(store.varietyPenalty).toBe(0)
    expect(store.customRules).toEqual([])
    expect(store.pendingTheme).toBe('')
    expect(store.loading).toBe(false)
    expect(store.saveStatus).toBe('idle')
  })

  it('load populates state from API', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: { genre_match: 3.0, tag_overlap: 0.5 },
      series_in_order: false,
      variety_penalty: 0.5,
      content_length_preferences: { book: 'short' },
      custom_rules: ['avoid horror'],
      theme: '',
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.scorerWeights).toEqual({ genre_match: 3.0, tag_overlap: 0.5 })
    expect(store.seriesInOrder).toBe(false)
    expect(store.varietyPenalty).toBe(0.5)
    expect(store.contentLengthPreferences).toEqual({ book: 'short' })
    expect(store.customRules).toEqual(['avoid horror'])
  })

  it('load defaults varietyPenalty to 0 when absent', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: {},
      series_in_order: true,
      content_length_preferences: {},
      custom_rules: [],
      theme: '',
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.varietyPenalty).toBe(0)
  })

  it('load reads varietyPenalty at the max (5.0)', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: {},
      series_in_order: true,
      variety_penalty: 5.0,
      content_length_preferences: {},
      custom_rules: [],
      theme: '',
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.varietyPenalty).toBe(5.0)
  })

  it('load applies saved theme and sets pendingTheme', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: {},
      series_in_order: true,
      variety_penalty: 0,
      custom_rules: [],
      content_length_preferences: {},
      theme: 'snowstorm',
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.pendingTheme).toBe('snowstorm')
    expect(mockApplyTheme).toHaveBeenCalledWith('snowstorm')
  })

  it('load falls back to defaultThemeId when no saved theme', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: {},
      series_in_order: true,
      variety_penalty: 0,
      custom_rules: [],
      content_length_preferences: {},
      theme: '',
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.pendingTheme).toBe('nord')
    expect(mockApplyTheme).not.toHaveBeenCalled()
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

  it('save sends preferences including theme to API', async () => {
    mockPut.mockResolvedValue({})

    const store = await loadedStore()
    store.scorerWeights = { genre_match: 3.0 }
    store.seriesInOrder = false
    store.varietyPenalty = 0.4
    store.customRules = ['prefer sci-fi']
    store.pendingTheme = 'snowstorm'

    await store.save()

    expect(mockPut).toHaveBeenCalledWith(
      '/users/1/preferences',
      expect.objectContaining({
        scorer_weights: { genre_match: 3.0 },
        series_in_order: false,
        variety_penalty: 0.4,
        custom_rules: ['prefer sci-fi'],
        theme: 'snowstorm',
      }),
    )
    expect(store.saveStatus).toBe('saved')
  })

  it('save sends varietyPenalty at the max (5.0)', async () => {
    mockPut.mockResolvedValue({})

    const store = await loadedStore()
    store.varietyPenalty = 5.0

    await store.save()

    expect(mockPut).toHaveBeenCalledWith(
      '/users/1/preferences',
      expect.objectContaining({ variety_penalty: 5.0 }),
    )
  })

  it('save sends varietyPenalty off (0.0)', async () => {
    mockPut.mockResolvedValue({})

    const store = await loadedStore()
    store.varietyPenalty = 0

    await store.save()

    expect(mockPut).toHaveBeenCalledWith(
      '/users/1/preferences',
      expect.objectContaining({ variety_penalty: 0 }),
    )
  })

  it('save applies theme only after successful save', async () => {
    mockPut.mockResolvedValue({})

    const store = await loadedStore()
    store.pendingTheme = 'snowstorm'
    await store.save()

    expect(mockApplyTheme).toHaveBeenCalledWith('snowstorm')
  })

  it('save does not apply theme on failure', async () => {
    mockPut.mockRejectedValue(new Error('Server error'))

    const store = await loadedStore()
    store.pendingTheme = 'snowstorm'
    await store.save()

    expect(mockApplyTheme).not.toHaveBeenCalled()
    expect(store.saveStatus).toBe('error')
    expect(store.saveError).toBe('Server error')
  })
})

// Symptom: a failed Preferences load then a Save wiped every stored rule and
// weight. Cause: load()'s catch reset the store to empty defaults, which save()
// PUT. Fix: keep the values, record loadError, gate save on hasLoaded.
describe('preferences load-failure regression', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockApplyTheme.mockReset()
  })

  it('keeps the previously loaded values when a later load fails', async () => {
    const store = await loadedStore()
    store.customRules = ['no horror']
    store.scorerWeights = { genre_match: 4.0 }
    mockGet.mockRejectedValue(new Error('Network error'))

    await store.load()

    expect(store.customRules).toEqual(['no horror'])
    expect(store.scorerWeights).toEqual({ genre_match: 4.0 })
    expect(store.loadError).toBe('Network error')
  })

})
