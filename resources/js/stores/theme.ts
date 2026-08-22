import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ThemeResponse, UserPreferenceResponse } from '@/types/api'

const STORAGE_KEY = 'theme'
const THEME_ID_RE = /^[a-zA-Z0-9_-]+$/

export const useThemeStore = defineStore('theme', () => {
  const api = useApi()

  const themes = ref<ThemeResponse[]>([])
  const currentThemeId = ref<string | null>(null)
  const defaultThemeId = ref('nord')

  /** The one thing that decides the theme: the stored preference, or the
   *  default when nothing has been picked. localStorage only caches it, so a
   *  pick made on another browser reaches this one here, at boot. */
  async function fetchThemes() {
    const app = useAppStore()
    try {
      const [themeList, defaultData, prefs] = await Promise.all([
        api.get<ThemeResponse[]>('/themes'),
        api.get<{ theme: string }>('/themes/default'),
        api.get<UserPreferenceResponse>(`/users/${app.currentUserId}/preferences`),
      ])

      if (themeList && themeList.length > 0) {
        themes.value = themeList
      }

      defaultThemeId.value = defaultData.theme || 'nord'

      // A theme folder removed after it was picked leaves the preference naming
      // nothing, and applyTheme would then paint no stylesheet at all.
      const stored = prefs.theme
      const installed = themes.value.length === 0 || themes.value.some((t) => t.id === stored)
      applyTheme(stored && installed ? stored : defaultThemeId.value)
    } catch {
      // Nothing to decide with: the browser keeps the theme it painted from cache.
    }
  }

  function applyStoredTheme() {
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
    // The stored preference confirming what the cache already painted must not
    // reload the stylesheet: that is the flash this was all built to avoid.
    if (themeId === currentThemeId.value) return

    if (themes.value.length > 0 && !themes.value.some((t) => t.id === themeId)) return

    const link = getOrCreateThemeLink()
    link.href = `/static/themes/${themeId}/colors.css`
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
