import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ThemePreferenceResponse, ThemeResponse } from '@/types/api'

const STORAGE_KEY = 'theme'
const THEME_ID_RE = /^[a-zA-Z0-9_-]+$/

export const useThemeStore = defineStore('theme', () => {
  const api = useApi()

  const themes = ref<ThemeResponse[]>([])
  const serverPaintedThemeId = document.documentElement.dataset.theme ?? null
  const currentThemeId = ref<string | null>(serverPaintedThemeId)
  const defaultThemeId = ref('nord')

  /** The one thing that decides the theme: the stored pick, or the default
   *  when nothing has been picked. localStorage only caches it, so a pick made
   *  on another browser reaches this one here, at boot. */
  async function fetchThemes() {
    const app = useAppStore()
    try {
      const [themeList, defaultData, stored] = await Promise.all([
        api.get<ThemeResponse[]>('/themes'),
        api.get<ThemePreferenceResponse>('/themes/default'),
        api
          .get<ThemePreferenceResponse>(`/users/${app.currentUserId}/theme`)
          .catch(() => null),
      ])

      if (themeList && themeList.length > 0) {
        themes.value = themeList
      }

      defaultThemeId.value = defaultData.theme || 'nord'

      // A read that failed is not a pick naming nothing: falling back would
      // repaint over the cache and overwrite it with the default.
      if (stored === null && currentThemeId.value) return

      const picked = stored?.theme
      const installed = themes.value.length === 0 || themes.value.some((t) => t.id === picked)
      applyTheme(picked && installed ? picked : defaultThemeId.value)
    } catch {
      // Nothing to decide with: the browser keeps the theme it painted from cache.
    }
  }

  function applyStoredTheme() {
    if (serverPaintedThemeId) return
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      applyTheme(stored)
    }
  }

  function getOrCreateThemeLink(): HTMLLinkElement {
    let link = document.getElementById('theme-stylesheet') as HTMLLinkElement | null
    if (!link) {
      link = document.createElement('link')
      link.id = 'theme-stylesheet'
      link.rel = 'stylesheet'
      // Append last in <head> so it overrides the Vite-bundled :root vars
      document.head.appendChild(link)
    }
    return link
  }

  function applyTheme(themeId: string) {
    if (!themeId || !THEME_ID_RE.test(themeId)) return

    const installed = themes.value.find((t) => t.id === themeId)
    if (themes.value.length > 0 && !installed) return

    // Keyed on the href, not the id: the cache paints a private theme at the
    // wrong url, and rewriting an unchanged one refetches it — the flash to avoid.
    const href = installed?.css_url ?? `/static/themes/${themeId}/colors.css`
    const link = getOrCreateThemeLink()
    if (link.getAttribute('href') !== href) link.setAttribute('href', href)
    document.documentElement.dataset.theme = themeId
    if (installed) document.documentElement.dataset.themeType = installed.theme_type
    localStorage.setItem(STORAGE_KEY, themeId)
    currentThemeId.value = themeId
  }

  return {
    themes,
    currentThemeId,
    defaultThemeId,
    fetchThemes,
    applyStoredTheme,
    applyTheme,
  }
})
