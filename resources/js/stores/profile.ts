import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type { ProfileResponse } from '@/types/api'

export const useProfileStore = defineStore('profile', () => {
  const api = useApi()

  const profile = ref<ProfileResponse | null>(null)
  const regenerating = ref(false)

  async function load() {
    const app = useAppStore()
    try {
      profile.value = await api.get<ProfileResponse>('/profile', {
        user_id: app.currentUserId,
      })
    } catch {
      // A user with no profile yet gets a 404; the panel shows its empty state.
      profile.value = null
    }
  }

  async function regenerate() {
    const app = useAppStore()
    regenerating.value = true
    try {
      profile.value = await api.post<ProfileResponse>('/profile/regenerate', {
        user_id: app.currentUserId,
      })
    } catch {
      // Keep the profile already on screen rather than blanking it.
    } finally {
      regenerating.value = false
    }
  }

  return {
    profile,
    regenerating,
    load,
    regenerate,
  }
})
