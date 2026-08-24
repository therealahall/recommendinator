import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import type { StatusResponse, RecommendationsConfig } from '@/types/api'

// A backend that is still coming up is worth asking again shortly; once it is
// ready the same call is only watching for a version it was not built against.
const WARMING_POLL_MS = 5_000
const READY_POLL_MS = 300_000

export const useAppStore = defineStore('app', () => {
  const api = useApi()

  // State
  // The claimed account is always the first row, and there is nobody to switch
  // to: a second person signs in with their own credentials.
  const currentUserId = ref(1)
  const status = ref<'loading' | 'ready' | 'error'>('loading')
  const statusMessage = ref('')
  const version = ref('')
  const loadedVersion = ref('')
  const showUpdateBanner = ref(false)
  const recommendationsConfig = ref<RecommendationsConfig>({
    max_count: 20,
    default_count: 5,
  })

  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let polling = false

  // Actions
  async function fetchStatus() {
    // Claimed before the request rather than after it: a 401 mid-flight signs
    // the user out, and stopPolling() has to beat this call booking the next.
    polling = true
    try {
      const data = await api.get<StatusResponse>('/status')
      const ready = data.status === 'ready'
      status.value = ready ? 'ready' : 'loading'
      statusMessage.value = ready ? '' : 'System initializing...'

      if (data.version) {
        version.value = data.version
        if (!loadedVersion.value) {
          loadedVersion.value = data.version
        }
        showUpdateBanner.value = data.version !== loadedVersion.value
      }

      if (data.recommendations_config) {
        recommendationsConfig.value = data.recommendations_config
      }
    } catch {
      status.value = 'error'
      statusMessage.value = 'Failed to connect to server'
    }
    if (polling) schedulePoll()
  }

  function schedulePoll() {
    if (pollTimer !== null) clearTimeout(pollTimer)
    pollTimer = setTimeout(
      fetchStatus,
      status.value === 'ready' ? READY_POLL_MS : WARMING_POLL_MS,
    )
  }

  function stopPolling() {
    polling = false
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  return {
    // State
    currentUserId,
    status,
    statusMessage,
    version,
    loadedVersion,
    showUpdateBanner,
    recommendationsConfig,
    // Actions
    fetchStatus,
    stopPolling,
  }
})
