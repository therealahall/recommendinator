import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePreferencesStore } from './preferences'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()
const mockApplyTheme = vi.fn()
let appliedThemeId: string | null = null

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

vi.mock('@/stores/theme', () => ({
  useThemeStore: () => ({
    applyTheme: mockApplyTheme,
    currentThemeId: appliedThemeId,
    defaultThemeId: 'nord',
  }),
}))

const STORED_PREFS = {
  scorer_weights: {},
  series_in_order: true,
  variety_penalty: 0,
  content_length_preferences: {},
  custom_rules: [],
}

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
    mockDelete.mockReset()
    mockApplyTheme.mockReset()
    appliedThemeId = null
  })

  it('load populates state from API', async () => {
    mockGet.mockResolvedValue({
      scorer_weights: { genre_match: 3.0, tag_overlap: 0.5 },
      series_in_order: false,
      variety_penalty: 0.5,
      content_length_preferences: { book: 'short' },
      custom_rules: ['avoid horror'],
    })

    const store = usePreferencesStore()
    await store.load()

    expect(store.scorerWeights).toEqual({ genre_match: 3.0, tag_overlap: 0.5 })
    expect(store.seriesInOrder).toBe(false)
    expect(store.varietyPenalty).toBe(0.5)
    expect(store.contentLengthPreferences).toEqual({ book: 'short' })
    expect(store.customRules).toEqual(['avoid horror'])
  })

  it('save sends the edited preferences and leaves nothing unsaved', async () => {
    mockPut.mockResolvedValue({})

    const store = await loadedStore()
    store.scorerWeights = { genre_match: 3.0 }
    store.seriesInOrder = false
    store.varietyPenalty = 0.4
    store.customRules = ['prefer sci-fi']
    expect(store.isDirty).toBe(true)

    await store.save()

    expect(mockPut).toHaveBeenCalledWith(
      '/users/1/preferences',
      expect.objectContaining({
        scorer_weights: { genre_match: 3.0 },
        series_in_order: false,
        variety_penalty: 0.4,
        custom_rules: ['prefer sci-fi'],
      }),
    )
    expect(store.saveStatus).toBe('saved')
    expect(store.isDirty).toBe(false)
  })

  it('an edit made while a save is in flight is still unsaved', async () => {
    let landed: () => void = () => {}
    mockPut.mockImplementation(
      () => new Promise<void>((resolve) => {
        landed = resolve
      }),
    )

    const store = await loadedStore()
    const inFlight = store.save()
    store.varietyPenalty = 2.5
    landed()
    await inFlight

    expect(store.isDirty).toBe(true)
  })

  it('a control moved away and back leaves nothing unsaved', async () => {
    const store = await loadedStore()

    store.contentLengthPreferences.book = 'short'
    expect(store.isDirty).toBe(true)
    store.contentLengthPreferences.book = 'any'

    expect(store.isDirty).toBe(false)
  })

  it('selecting a theme applies it at once and stores it without a save', async () => {
    mockPut.mockResolvedValue({})
    const store = await loadedStore()

    await store.selectTheme('snowstorm')

    expect(mockApplyTheme).toHaveBeenCalledWith('snowstorm')
    expect(mockPut).toHaveBeenCalledWith('/users/1/theme', { theme: 'snowstorm' })
    expect(store.isDirty).toBe(false)
  })

  it('stores a theme picked before the preferences load, which it no longer rides with', async () => {
    mockGet.mockRejectedValue(new Error('Network error'))
    mockPut.mockResolvedValue({})
    const store = usePreferencesStore()
    await store.load()

    await store.selectTheme('snowstorm')

    expect(mockPut).toHaveBeenCalledWith('/users/1/theme', { theme: 'snowstorm' })
  })

  it('load leaves the theme alone: boot already applied the stored one', async () => {
    appliedThemeId = 'snowstorm'
    mockGet.mockResolvedValue(STORED_PREFS)

    await usePreferencesStore().load()

    expect(mockApplyTheme).not.toHaveBeenCalled()
  })

  it('reset shows the defaults the server answers with, and leaves the theme painted', async () => {
    const store = await loadedStore()
    store.customRules = ['no horror']
    store.varietyPenalty = 3.0
    mockDelete.mockResolvedValue(STORED_PREFS)

    await store.resetToDefaults()

    expect(mockDelete).toHaveBeenCalledWith('/users/1/preferences')
    expect(store.customRules).toEqual([])
    expect(store.varietyPenalty).toBe(0)
    expect(store.isDirty).toBe(false)
    expect(mockApplyTheme).not.toHaveBeenCalled()
  })
})

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

  it('sends nothing from a store that never loaded, whichever Save was pressed', async () => {
    const store = usePreferencesStore()

    await store.save()
    expect(mockPut).not.toHaveBeenCalled()

    mockGet.mockRejectedValue(new Error('Network error'))
    await store.load()
    await store.save()

    expect(mockPut).not.toHaveBeenCalled()
    expect(store.saveError).toBe('Preferences have not loaded yet.')
  })
})
