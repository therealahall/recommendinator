import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const STORAGE_KEY = 'apiToken'

export const useAuthStore = defineStore('auth', () => {
  // Seeded from storage so a reload does not ask again. Persisted rather than
  // held in memory because every request needs it and the alternative is
  // retyping it on every navigation.
  const token = ref(localStorage.getItem(STORAGE_KEY) ?? '')
  const rejected = ref(false)

  const isAuthenticated = computed(() => token.value !== '')

  function setToken(value: string) {
    token.value = value.trim()
    rejected.value = false
    localStorage.setItem(STORAGE_KEY, token.value)
  }

  function clearToken() {
    token.value = ''
    localStorage.removeItem(STORAGE_KEY)
  }

  /** Drop a token the server refused, so the gate asks for another one. */
  function reject() {
    clearToken()
    rejected.value = true
  }

  return {
    // State
    token,
    rejected,
    // Getters
    isAuthenticated,
    // Actions
    setToken,
    clearToken,
    reject,
  }
})
