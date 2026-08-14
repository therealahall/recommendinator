import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'
import type { StatusResponse, RecommendationsConfig } from '@/types/api'

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

  let versionPollTimer: ReturnType<typeof setInterval> | null = null

  // Actions
  async function fetchStatus() {
    try {
      const data = await api.get<StatusResponse>('/status')
      status.value = data.status === 'ready' ? 'ready' : 'loading'
      statusMessage.value = data.status === 'ready' ? '' : 'System initializing...'

      if (data.version) {
        version.value = data.version
        if (!loadedVersion.value) {
          loadedVersion.value = data.version
        }
      }

      if (data.recommendations_config) {
        recommendationsConfig.value = data.recommendations_config
      }

      startVersionPolling()
    } catch {
      status.value = 'error'
      statusMessage.value = 'Failed to connect to server'
    }
  }

  function startVersionPolling() {
    if (versionPollTimer !== null) return
    versionPollTimer = setInterval(async () => {
      try {
        const data = await api.get<StatusResponse>('/status')
        if (data.version && data.version !== loadedVersion.value) {
          showUpdateBanner.value = true
        }
      } catch {
        // Silently ignore polling errors
      }
    }, 300_000) // 5 minutes
  }

  function stopVersionPolling() {
    if (versionPollTimer !== null) {
      clearInterval(versionPollTimer)
      versionPollTimer = null
    }
  }

  function dismissStatus() {
    statusMessage.value = ''
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
    startVersionPolling,
    stopVersionPolling,
    dismissStatus,
  }
})
