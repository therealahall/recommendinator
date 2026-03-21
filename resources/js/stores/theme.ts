import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import type { ThemeResponse } from '@/types/api'

const STORAGE_KEY = 'theme'
const THEME_ID_RE = /^[a-zA-Z0-9_-]+$/

export const useThemeStore = defineStore('theme', () => {
  const api = useApi()

  // State
  const themes = ref<ThemeResponse[]>([])
  const currentThemeId = ref<string | null>(null)
  const defaultThemeId = ref('nord')

  // Actions
  async function fetchThemes() {
    try {
      const [themeList, defaultData] = await Promise.all([
        api.get<ThemeResponse[]>('/themes'),
        api.get<{ theme: string }>('/themes/default'),
      ])

      if (themeList && themeList.length > 0) {
        themes.value = themeList
      }

      defaultThemeId.value = defaultData.theme || 'nord'

      // Apply config default if no localStorage preference
      const stored = localStorage.getItem(STORAGE_KEY)
      if (!stored && defaultData.theme) {
        applyTheme(defaultData.theme)
      }
    } catch {
      // Silently ignore if themes endpoint not available
    }
  }

  function applyStoredTheme() {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      applyTheme(stored)
    }
  }

  function applyTheme(themeId: string) {
    if (!themeId || !THEME_ID_RE.test(themeId)) return

    // Validate against known themes when cache is available
    if (themes.value.length > 0) {
      const known = themes.value.some((t) => t.id === themeId)
      if (!known) return
    }

    const link = document.getElementById('theme-stylesheet') as HTMLLinkElement | null
    if (!link) return

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
