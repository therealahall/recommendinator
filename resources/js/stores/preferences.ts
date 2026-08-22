import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { useThemeStore } from '@/stores/theme'
import type { UserPreferenceResponse, UserPreferenceUpdateRequest } from '@/types/api'

export const SCORER_KEYS = [
  'genre_match',
  'creator_match',
  'tag_overlap',
  'series_order',
  'rating_pattern',
  'content_length',
  'continuation',
  'series_affinity',
  'adaptation',
  'custom_preference',
] as const

export const DEFAULT_WEIGHTS: Record<string, number> = {
  genre_match: 2.0,
  creator_match: 1.5,
  tag_overlap: 1.0,
  series_order: 1.5,
  rating_pattern: 1.0,
  content_length: 1.0,
  continuation: 2.0,
  series_affinity: 1.0,
  adaptation: 1.5,
  custom_preference: 1.0,
}

export const SCORER_TOOLTIPS: Record<string, string> = {
  genre_match: 'Scores recommendations by how well their genres match your preferences. Higher weight means genre alignment matters more. Default: 2.0',
  creator_match: "Boosts items by creators (authors, directors, developers) you've enjoyed before. Default: 1.5",
  tag_overlap: "Scores items by how many tags and genres they share with things you've consumed. Related genres count too, grouped into clusters like 'fantasy' and 'sword and sorcery'. Default: 1.0",
  series_order: "Prioritizes the next item in a series you've started (e.g. Fallout 1, then Fallout 2). Scores higher when you rated earlier entries well. Default: 1.5",
  rating_pattern: "Uses your rating history per genre to predict how much you'd enjoy a recommendation. Genres you rate highly get boosted. Default: 1.0",
  content_length: 'Soft-penalizes items that don\'t match your preferred content length (short/medium/long) per content type. Default: 1.0',
  continuation: "Strongly boosts items you're currently consuming (e.g. a TV show you're mid-way through). Default: 2.0",
  series_affinity: "Boosts items from franchises you've rated highly (avg 4+ stars). Keeps recommending series you love. Default: 1.0",
  adaptation: 'Boosts a film, show or game that adapts something you rated well, and the book behind an adaptation you loved. Default: 1.5',
  custom_preference: 'Applies your natural language rules, the "avoid X" and "prefer Y" ones, from the Rules section below. Default: 1.0',
}

// The "5.0 at full strength" mirrors the backend UserPreferenceConfig.MAX_VARIETY_PENALTY.
// There is no runtime way to reference that Python constant from TS, so if the
// backend maximum changes, update this copy to match.
export const VARIETY_PENALTY_TOOLTIP =
  'After you finish something, demotes further recommendations of the same content type, encouraging variety. 0 turns it off; higher values push harder, up to 5.0 at full strength. The penalty is strongest on the genre you just finished and decays as you complete more.'

export const CONTENT_TYPES = ['book', 'movie', 'tv_show', 'video_game'] as const
export const LENGTH_OPTIONS = ['any', 'short', 'medium', 'long'] as const

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown error'
}

export const usePreferencesStore = defineStore('preferences', () => {
  const api = useApi()

  // State
  const scorerWeights = ref<Record<string, number>>({})
  const seriesInOrder = ref(true)
  const varietyPenalty = ref(0)
  const contentLengthPreferences = ref<Record<string, string>>({})
  const customRules = ref<string[]>([])
  const loading = ref(false)
  const loadError = ref('')
  // True only while the values above came from the server. Everything is PUT
  // unconditionally on save, so saving from any other state overwrites the
  // stored preferences with this store's empty defaults.
  const hasLoaded = ref(false)
  const saving = ref(false)
  const saveStatus = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const saveError = ref('')
  const savedFields = ref('')

  /** Both maps are sparse — an absent key means the default — so the absent
   *  ones are resolved the way the page renders them, or a control moved away
   *  and back would read as an edit. */
  function fields(): string {
    return JSON.stringify([
      SCORER_KEYS.map(getWeight),
      seriesInOrder.value,
      varietyPenalty.value,
      CONTENT_TYPES.map((type) => contentLengthPreferences.value[type] || 'any'),
      customRules.value,
    ])
  }

  /** Edits the Save button still holds: nothing here is written until it is
   *  pressed, so leaving the page throws them away. */
  const isDirty = computed(() => hasLoaded.value && fields() !== savedFields.value)

  function adopt(prefs: UserPreferenceResponse) {
    scorerWeights.value = prefs.scorer_weights
    seriesInOrder.value = prefs.series_in_order
    varietyPenalty.value = prefs.variety_penalty ?? 0
    contentLengthPreferences.value = prefs.content_length_preferences || {}
    customRules.value = prefs.custom_rules || []
    hasLoaded.value = true
    savedFields.value = fields()
  }

  function reportSaved() {
    saveStatus.value = 'saved'
    setTimeout(() => {
      saveStatus.value = 'idle'
    }, 2000)
  }

  // Actions
  async function load() {
    const app = useAppStore()
    loading.value = true
    loadError.value = ''
    hasLoaded.value = false
    try {
      const prefs = await api.get<UserPreferenceResponse>(
        `/users/${app.currentUserId}/preferences`,
      )
      adopt(prefs)

      // Boot paints the config default on every browser, so what is on screen
      // is no evidence of a pick. Picking stores one, which this reads back.
      const theme = useThemeStore()
      if (prefs.theme && prefs.theme !== theme.currentThemeId) theme.applyTheme(prefs.theme)
    } catch (err) {
      loadError.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  /** Apply a theme at once and persist it in the background: one a user has to
   *  press Save to see is one they cannot try on. */
  async function selectTheme(themeId: string) {
    const theme = useThemeStore()
    theme.applyTheme(themeId)
    // Without a loaded config there is nothing to merge into, and localStorage
    // holds the theme either way, so this browser keeps it.
    if (!hasLoaded.value) return

    const app = useAppStore()
    const payload: UserPreferenceUpdateRequest = { theme: themeId }
    try {
      await api.put(`/users/${app.currentUserId}/preferences`, payload)
    } catch (err) {
      // Applied but not stored: another browser still shows the old theme, and
      // saying nothing reads as saved.
      saveStatus.value = 'error'
      saveError.value = errorMessage(err)
    }
  }

  /** The web door to ``preferences reset``. The server answers with the
   *  defaults it wrote, so the page shows them without a reload. */
  async function resetToDefaults() {
    const app = useAppStore()
    saving.value = true
    saveStatus.value = 'saving'
    try {
      const defaults = await api.delete<UserPreferenceResponse>(
        `/users/${app.currentUserId}/preferences`,
      )
      adopt(defaults)
      // The CLI's reset clears the stored theme too, so leaving this one
      // applied would make the surfaces disagree about the defaults.
      const theme = useThemeStore()
      theme.applyTheme(theme.defaultThemeId)
      reportSaved()
    } catch (err) {
      saveStatus.value = 'error'
      saveError.value = errorMessage(err)
    } finally {
      saving.value = false
    }
  }

  async function save() {
    if (!hasLoaded.value) {
      saveStatus.value = 'error'
      saveError.value = 'Preferences have not loaded yet.'
      return
    }
    const app = useAppStore()
    saving.value = true
    saveStatus.value = 'saving'
    try {
      const payload: UserPreferenceUpdateRequest = {
        scorer_weights: scorerWeights.value,
        series_in_order: seriesInOrder.value,
        variety_penalty: varietyPenalty.value,
        content_length_preferences: contentLengthPreferences.value,
        custom_rules: customRules.value,
      }
      await api.put(`/users/${app.currentUserId}/preferences`, payload)
      // The theme is not in the payload: it was stored when it was picked, and
      // sending a stale copy here would undo a selection made since.
      savedFields.value = fields()
      reportSaved()
    } catch (err) {
      saveStatus.value = 'error'
      saveError.value = errorMessage(err)
    } finally {
      saving.value = false
    }
  }

  function getWeight(key: string): number {
    const raw = scorerWeights.value[key]
    if (raw !== undefined && isFinite(raw)) return raw
    return DEFAULT_WEIGHTS[key] ?? 1.0
  }

  function setWeight(key: string, value: number) {
    scorerWeights.value[key] = value
  }

  function addRule(rule: string) {
    const trimmed = rule.trim()
    if (trimmed) {
      customRules.value.push(trimmed)
    }
  }

  function removeRule(index: number) {
    customRules.value.splice(index, 1)
  }

  return {
    scorerWeights,
    seriesInOrder,
    varietyPenalty,
    contentLengthPreferences,
    customRules,
    loading,
    loadError,
    hasLoaded,
    isDirty,
    saving,
    saveStatus,
    saveError,
    load,
    save,
    selectTheme,
    resetToDefaults,
    getWeight,
    setWeight,
    addRule,
    removeRule,
  }
})
