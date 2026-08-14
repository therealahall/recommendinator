import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ProfileResponse } from '@/types/api'

export const useProfileStore = defineStore('profile', () => {
  const api = useApi()

  const profile = ref<ProfileResponse | null>(null)
  const regenerating = ref(false)
  const error = ref('')

  // Regenerating an unrated library stamps generated_at over an empty body, so
  // the timestamp cannot tell an absent profile from a vacuous one.
  function hasContent(response: ProfileResponse): boolean {
    return (
      Object.keys(response.genre_affinities).length > 0 ||
      response.theme_preferences.length > 0 ||
      response.anti_preferences.length > 0 ||
      response.cross_media_patterns.length > 0
    )
  }

  async function load() {
    const app = useAppStore()
    error.value = ''
    try {
      const response = await api.get<ProfileResponse>('/profile', {
        user_id: app.currentUserId,
      })
      profile.value = hasContent(response) ? response : null
    } catch (err) {
      // Surfaced rather than swallowed: an empty panel is also what a library
      // with nothing rated looks like, so a failure is invisible otherwise.
      profile.value = null
      error.value = err instanceof Error ? err.message : 'Failed to load profile'
    }
  }

  async function regenerate() {
    const app = useAppStore()
    regenerating.value = true
    error.value = ''
    try {
      const response = await api.post<ProfileResponse>('/profile/regenerate', {
        user_id: app.currentUserId,
      })
      profile.value = hasContent(response) ? response : null
    } catch (err) {
      // Keep the profile already on screen rather than blanking it.
      error.value = err instanceof Error ? err.message : 'Failed to regenerate profile'
    } finally {
      regenerating.value = false
    }
  }

  return {
    profile,
    regenerating,
    error,
    load,
    regenerate,
  }
})
