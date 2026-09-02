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
  {
    id: 'nord',
    name: 'Nord',
    description: '',
    author: '',
    version: '1.0.0',
    theme_type: 'dark',
    css_url: '/static/themes/nord/colors.css',
  },
  {
    id: 'snowstorm',
    name: 'Snowstorm',
    description: '',
    author: '',
    version: '1.0.0',
    theme_type: 'light',
    css_url: '/static/themes/snowstorm/colors.css',
  },
]

describe('useThemeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    mockGet.mockReset()
    delete document.documentElement.dataset.theme
    delete document.documentElement.dataset.themeType
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

    expect(store.currentThemeId).toBeNull()
    expect(localStorage.getItem('theme')).toBeNull()
  })

  it('applyTheme validates against known themes', () => {
    const store = useThemeStore()
    store.themes = [THEMES[0]]
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

  it('keeps the theme the shell resolved from the OS when nothing was picked', async () => {
    // Regression: boot repainted the config default over the theme the
    // pre-paint script had chosen from prefers-color-scheme.
    const rendered = renderedShellLink('snowstorm')
    answerBoot('nord', '')

    const store = useThemeStore()
    await store.fetchThemes()

    expect(store.currentThemeId).toBe('snowstorm')
    expect(rendered.href).toContain('/static/themes/snowstorm/colors.css')
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

  it('leaves the cached pick painted when only the preference read fails', async () => {
    localStorage.setItem('theme', 'snowstorm')
    mockGet.mockImplementation((path: string) => {
      if (path === '/themes') return Promise.resolve(THEMES)
      if (path === '/themes/default') return Promise.resolve({ theme: 'nord' })
      return Promise.reject(new Error('Internal Server Error'))
    })

    const store = useThemeStore()
    store.applyStoredTheme()
    await store.fetchThemes()

    expect(store.currentThemeId).toBe('snowstorm')
    expect(localStorage.getItem('theme')).toBe('snowstorm')
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

  it('leaves the theme the server rendered into the shell alone', () => {
    document.documentElement.dataset.theme = 'snowstorm'
    localStorage.setItem('theme', 'nord')

    const store = useThemeStore()
    store.applyStoredTheme()

    expect(store.currentThemeId).toBe('snowstorm')
    expect(document.getElementById('theme-stylesheet')).toBeNull()
  })

  function renderedShellLink(themeId: string): HTMLLinkElement {
    const rendered = document.createElement('link')
    rendered.id = 'theme-stylesheet'
    rendered.rel = 'stylesheet'
    rendered.href = `/static/themes/${themeId}/colors.css`
    document.head.appendChild(rendered)
    document.documentElement.dataset.theme = themeId
    return rendered
  }

  it('switching themes over and over reuses the one link the server rendered', () => {
    const rendered = renderedShellLink('nord')

    const store = useThemeStore()
    store.themes = THEMES
    store.applyTheme('snowstorm')
    store.applyTheme('nord')
    store.applyTheme('snowstorm')

    expect(document.querySelectorAll('#theme-stylesheet')).toHaveLength(1)
    expect(rendered.href).toContain('/static/themes/snowstorm/colors.css')
    expect(document.documentElement.dataset.themeType).toBe('light')
  })

  it('boot does not rewrite the href the server already rendered', async () => {
    const rendered = renderedShellLink('nord')
    const rewrite = vi.spyOn(rendered, 'setAttribute')
    answerBoot('nord', 'nord')

    const store = useThemeStore()
    store.applyStoredTheme()
    await store.fetchThemes()

    expect(rewrite).not.toHaveBeenCalled()
    expect(store.currentThemeId).toBe('nord')
  })

  it('corrects a cached private theme onto the url it is really served from', async () => {
    localStorage.setItem('theme', 'midnight')
    const midnight = {
      ...THEMES[0],
      id: 'midnight',
      css_url: '/static/private-themes/midnight/colors.css',
    }
    mockGet.mockImplementation((path: string) => {
      if (path === '/themes') return Promise.resolve([midnight])
      if (path === '/themes/default') return Promise.resolve({ theme: 'nord' })
      return Promise.resolve({ theme: 'midnight' })
    })

    const store = useThemeStore()
    store.applyStoredTheme()
    await store.fetchThemes()

    const link = document.getElementById('theme-stylesheet') as HTMLLinkElement
    expect(link.href).toContain('/static/private-themes/midnight/colors.css')
  })
})
