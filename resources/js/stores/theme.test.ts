import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useThemeStore } from './theme'

const mockGet = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

const THEMES = [
  { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
  { id: 'snowstorm', name: 'Snowstorm', description: '', author: '', version: '1.0.0', theme_type: 'light' },
]

describe('useThemeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    mockGet.mockReset()
    // Remove any leftover theme-stylesheet from previous tests
    const existing = document.getElementById('theme-stylesheet')
    if (existing) existing.remove()
  })

  afterEach(() => {
    const el = document.getElementById('theme-stylesheet')
    if (el) el.remove()
  })

  it('applyTheme sets link href and localStorage', () => {
    const store = useThemeStore()
    store.applyTheme('snowstorm')

    const link = document.getElementById('theme-stylesheet') as HTMLLinkElement
    expect(link.href).toContain('/static/themes/snowstorm/colors.css')
    expect(localStorage.getItem('theme')).toBe('snowstorm')
    expect(store.currentThemeId).toBe('snowstorm')
  })

  it('applyTheme rejects invalid theme IDs', () => {
    const store = useThemeStore()
    store.applyTheme('../evil')

    // Should not have changed currentThemeId
    expect(store.currentThemeId).toBeNull()
    expect(localStorage.getItem('theme')).toBeNull()
  })

  it('applyTheme validates against known themes', () => {
    const store = useThemeStore()
    store.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]
    store.applyTheme('unknown')

    expect(store.currentThemeId).toBeNull()
  })

  function answerBoot(configDefault: string, stored: string) {
    mockGet.mockImplementation((path: string) => {
      if (path === '/themes') return Promise.resolve(THEMES)
      if (path === '/themes/default') return Promise.resolve({ theme: configDefault })
      return Promise.resolve({ theme: stored })
    })
  }

  it('fetchThemes applies the config default when nothing has been picked', async () => {
    answerBoot('snowstorm', '')

    const store = useThemeStore()
    await store.fetchThemes()

    expect(store.themes).toEqual(THEMES)
    expect(store.defaultThemeId).toBe('snowstorm')
    expect(store.currentThemeId).toBe('snowstorm')
  })

  it('falls back to the default when the stored theme is gone', async () => {
    // Regression: boot resolves the theme from the stored preference, and a
    // theme folder removed after it was picked left that preference naming
    // nothing, so a browser with no cache painted no theme at all.
    answerBoot('nord', 'retired')

    const store = useThemeStore()
    await store.fetchThemes()

    expect(store.currentThemeId).toBe('nord')
  })

  it('repaints the default over a cached theme that is gone', async () => {
    localStorage.setItem('theme', 'retired')
    answerBoot('nord', 'retired')

    const store = useThemeStore()
    store.applyStoredTheme()
    await store.fetchThemes()

    const link = document.getElementById('theme-stylesheet') as HTMLLinkElement
    expect(link.href).toContain('/static/themes/nord/colors.css')
  })

  it('a server that cannot be reached leaves the cached theme painted', async () => {
    localStorage.setItem('theme', 'nord')
    mockGet.mockRejectedValue(new Error('Network error'))

    const store = useThemeStore()
    store.applyStoredTheme()
    await store.fetchThemes()

    expect(store.currentThemeId).toBe('nord')
  })

  it('keeps the theme list when only the preference read fails', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/themes') return Promise.resolve(THEMES)
      if (path === '/themes/default') return Promise.resolve({ theme: 'nord' })
      return Promise.reject(new Error('Internal Server Error'))
    })

    const store = useThemeStore()
    await store.fetchThemes()

    expect(store.themes).toEqual(THEMES)
    expect(store.currentThemeId).toBe('nord')
  })
})
