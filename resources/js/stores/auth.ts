import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const STORAGE_KEY = 'apiToken'
const VERIFY_URL = '/api/status'

/** 'rejected' is the server refusing the token with a 401. 'unreachable' is
 *  every other unconfirmed outcome — no answer, or an answer that settles
 *  nothing — which the gate has to word differently. */
export type AuthStatus = 'idle' | 'verifying' | 'rejected' | 'unreachable'

export const useAuthStore = defineStore('auth', () => {
  // Seeded from storage so a reload does not ask again. Persisted rather than
  // held in memory because every request needs it and the alternative is
  // retyping it on every navigation.
  const token = ref(localStorage.getItem(STORAGE_KEY) ?? '')
  const status = ref<AuthStatus>('idle')

  // False for the whole round trip. Unlocking on submit destroyed the gate,
  // fired the shell's requests at an unchecked token, and rebuilt the gate on
  // the 401, too late for its live region to announce anything.
  const isAuthenticated = computed(
    () => token.value !== '' && status.value !== 'verifying',
  )

  /** Drop a token the server refused, so the gate asks for another one. */
  function reject() {
    token.value = ''
    localStorage.removeItem(STORAGE_KEY)
    status.value = 'rejected'
  }

  /** Check a candidate against an authenticated endpoint and persist it only
   *  once that call comes back. True means the app may unlock. */
  async function submitToken(value: string): Promise<boolean> {
    const candidate = value.trim()
    if (!candidate || status.value === 'verifying') return false

    status.value = 'verifying'

    let response: Response
    try {
      // Deliberately not useApi: this asks about the candidate rather than the
      // stored token, and importing the composable that imports this store
      // would close a module cycle.
      response = await fetch(VERIFY_URL, { headers: { Authorization: `Bearer ${candidate}` } })
    } catch {
      status.value = 'unreachable'
      return false
    }

    if (response.status === 401) {
      reject()
      return false
    }
    if (!response.ok) {
      // Any other refusal says nothing about the token, so it is neither kept
      // nor called wrong.
      status.value = 'unreachable'
      return false
    }

    token.value = candidate
    localStorage.setItem(STORAGE_KEY, candidate)
    status.value = 'idle'
    return true
  }

  return {
    // State
    token,
    status,
    // Getters
    isAuthenticated,
    // Actions
    submitToken,
    reject,
  }
})
