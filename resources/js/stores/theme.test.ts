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
    raw: vi.fn(),
  }),
}))

describe('useThemeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    mockGet.mockReset()
    // Ensure a fresh theme-stylesheet element
    const existing = document.getElementById('theme-stylesheet')
    if (existing) existing.remove()
    const link = document.createElement('link')
    link.id = 'theme-stylesheet'
    document.head.appendChild(link)
  })

  afterEach(() => {
    const el = document.getElementById('theme-stylesheet')
    if (el) el.remove()
  })

  it('has correct initial state', () => {
    const store = useThemeStore()
    expect(store.themes).toEqual([])
    expect(store.currentThemeId).toBeNull()
    expect(store.defaultThemeId).toBe('nord')
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

  it('applyStoredTheme reads from localStorage', () => {
    localStorage.setItem('theme', 'snowstorm')
    const store = useThemeStore()
    store.applyStoredTheme()

    expect(store.currentThemeId).toBe('snowstorm')
  })

  it('fetchThemes loads themes and applies default', async () => {
    const themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
      { id: 'snowstorm', name: 'Snowstorm', description: '', author: '', version: '1.0.0', theme_type: 'light' },
    ]
    mockGet
      .mockResolvedValueOnce(themes)
      .mockResolvedValueOnce({ theme: 'snowstorm' })

    const store = useThemeStore()
    await store.fetchThemes()

    expect(store.themes).toEqual(themes)
    expect(store.defaultThemeId).toBe('snowstorm')
    // Should apply default since no localStorage preference
    expect(store.currentThemeId).toBe('snowstorm')
  })

  it('fetchThemes does not override localStorage preference', async () => {
    localStorage.setItem('theme', 'nord')
    const themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]
    mockGet
      .mockResolvedValueOnce(themes)
      .mockResolvedValueOnce({ theme: 'snowstorm' })

    const store = useThemeStore()
    await store.fetchThemes()

    // Should NOT apply default because localStorage has a preference
    expect(store.defaultThemeId).toBe('snowstorm')
    // currentThemeId stays null because applyStoredTheme wasn't called
    expect(store.currentThemeId).toBeNull()
  })
})
