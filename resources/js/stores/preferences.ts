import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { UserPreferenceResponse, UserPreferenceUpdateRequest } from '@/types/api'

export const SCORER_KEYS = [
  'genre_match',
  'creator_match',
  'tag_overlap',
  'series_order',
  'rating_pattern',
  'semantic_similarity',
  'content_length',
  'continuation',
  'series_affinity',
] as const

export const DEFAULT_WEIGHTS: Record<string, number> = {
  genre_match: 2.0,
  creator_match: 1.5,
  tag_overlap: 1.0,
  series_order: 1.5,
  rating_pattern: 1.0,
  semantic_similarity: 1.5,
  content_length: 1.0,
  continuation: 2.0,
  series_affinity: 1.0,
}

export const SCORER_TOOLTIPS: Record<string, string> = {
  genre_match: 'Scores recommendations by how well their genres match your preferences. Higher weight means genre alignment matters more. Default: 2.0',
  creator_match: "Boosts items by creators (authors, directors, developers) you've enjoyed before. Default: 1.5",
  tag_overlap: "Scores items by how many tags and genres they share with things you've consumed. Also uses semantic genre clusters for fuzzy matching. Default: 1.0",
  series_order: "Prioritizes the next item in a series you've started (e.g. Fallout 1, then Fallout 2). Scores higher when you rated earlier entries well. Default: 1.5",
  rating_pattern: "Uses your rating history per genre to predict how much you'd enjoy a recommendation. Genres you rate highly get boosted. Default: 1.0",
  semantic_similarity: "Uses AI embeddings to find items that are semantically similar to what you've enjoyed, even when tags don't overlap. Requires AI features. Default: 1.5",
  content_length: 'Soft-penalizes items that don\'t match your preferred content length (short/medium/long) per content type. Default: 1.0',
  continuation: "Strongly boosts items you're currently consuming (e.g. a TV show you're mid-way through). Default: 2.0",
  series_affinity: "Boosts items from franchises you've rated highly (avg 4+ stars). Keeps recommending series you love. Default: 1.0",
}

export const CONTENT_TYPES = ['book', 'movie', 'tv_show', 'video_game'] as const
export const LENGTH_OPTIONS = ['any', 'short', 'medium', 'long'] as const

export const usePreferencesStore = defineStore('preferences', () => {
  const api = useApi()

  // State
  const scorerWeights = ref<Record<string, number>>({})
  const seriesInOrder = ref(true)
  const varietyAfterCompletion = ref(false)
  const contentLengthPreferences = ref<Record<string, string>>({})
  const customRules = ref<string[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const saveStatus = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const saveError = ref('')

  // Actions
  async function load() {
    const app = useAppStore()
    loading.value = true
    try {
      const prefs = await api.get<UserPreferenceResponse>(
        `/users/${app.currentUserId}/preferences`,
      )
      scorerWeights.value = prefs.scorer_weights
      seriesInOrder.value = prefs.series_in_order
      varietyAfterCompletion.value = prefs.variety_after_completion
      contentLengthPreferences.value = prefs.content_length_preferences || {}
      customRules.value = prefs.custom_rules || []
    } catch {
      // Use defaults on error
      scorerWeights.value = {}
      seriesInOrder.value = true
      varietyAfterCompletion.value = false
      contentLengthPreferences.value = {}
      customRules.value = []
    } finally {
      loading.value = false
    }
  }

  async function save() {
    const app = useAppStore()
    saving.value = true
    saveStatus.value = 'saving'
    try {
      const payload: UserPreferenceUpdateRequest = {
        scorer_weights: scorerWeights.value,
        series_in_order: seriesInOrder.value,
        variety_after_completion: varietyAfterCompletion.value,
        content_length_preferences: contentLengthPreferences.value,
        custom_rules: customRules.value,
      }
      await api.put(`/users/${app.currentUserId}/preferences`, payload)
      saveStatus.value = 'saved'
      setTimeout(() => {
        saveStatus.value = 'idle'
      }, 2000)
    } catch (err) {
      saveStatus.value = 'error'
      saveError.value = err instanceof Error ? err.message : 'Unknown error'
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
    varietyAfterCompletion,
    contentLengthPreferences,
    customRules,
    loading,
    saving,
    saveStatus,
    saveError,
    load,
    save,
    getWeight,
    setWeight,
    addRule,
    removeRule,
  }
})
