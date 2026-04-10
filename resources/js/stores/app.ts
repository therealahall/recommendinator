import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useApi } from '@/composables/useApi'
import type { StatusResponse, UserResponse, FeaturesStatus, RecommendationsConfig } from '@/types/api'

export const useAppStore = defineStore('app', () => {
  const api = useApi()

  // State
  const users = ref<UserResponse[]>([])
  const currentUserId = ref(1)
  const status = ref<'loading' | 'ready' | 'error'>('loading')
  const statusMessage = ref('')
  const version = ref('')
  const loadedVersion = ref('')
  const showUpdateBanner = ref(false)
  const features = ref<FeaturesStatus>({
    ai_enabled: false,
    embeddings_enabled: false,
    llm_reasoning_enabled: false,
  })
  const recommendationsConfig = ref<RecommendationsConfig>({
    max_count: 20,
    default_count: 5,
  })

  let versionPollTimer: ReturnType<typeof setInterval> | null = null

  // Getters
  const currentUser = computed(() =>
    users.value.find((u) => u.id === currentUserId.value),
  )
  const chatEnabled = computed(() => features.value.ai_enabled)
  const aiReasoningEnabled = computed(
    () => features.value.ai_enabled && features.value.llm_reasoning_enabled,
  )

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

      if (data.features) {
        features.value = data.features
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

  async function fetchUsers() {
    try {
      users.value = await api.get<UserResponse[]>('/users')
    } catch {
      // Silently ignore if users endpoint not available
    }
  }

  function setUser(userId: number) {
    currentUserId.value = userId
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
    users,
    currentUserId,
    status,
    statusMessage,
    version,
    loadedVersion,
    showUpdateBanner,
    features,
    recommendationsConfig,
    // Getters
    currentUser,
    chatEnabled,
    aiReasoningEnabled,
    // Actions
    fetchStatus,
    fetchUsers,
    setUser,
    startVersionPolling,
    stopVersionPolling,
    dismissStatus,
  }
})
