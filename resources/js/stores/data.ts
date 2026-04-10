import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi, ApiError } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { truncate } from '@/utils/format'
import type {
  SyncSourceResponse,
  SyncStatusResponse,
  SyncJobResponse,
  EnrichmentStatsResponse,
  EnrichmentJobStatusResponse,
  AuthStatusResponse,
} from '@/types/api'

export const useDataStore = defineStore('data', () => {
  const api = useApi()

  // Sync state
  const syncSources = ref<SyncSourceResponse[]>([])
  const syncStatus = ref<'idle' | 'running' | 'completed' | 'failed'>('idle')
  const syncingSource = ref<string | null>(null)
  const syncJob = ref<SyncJobResponse | null>(null)
  // syncMessage may contain server-supplied source IDs — render with {{ }} only, never v-html
  const syncMessage = ref('')
  const syncLoading = ref(false)

  // Auth state
  const gogStatus = ref<{ authUrl: string | null; connected: boolean }>({ authUrl: null, connected: false })
  const epicStatus = ref<{ authUrl: string | null; connected: boolean }>({ authUrl: null, connected: false })
  const gogConnectMessage = ref('')
  const epicConnectMessage = ref('')

  // Enrichment state
  const enrichmentStats = ref<EnrichmentStatsResponse | null>(null)
  const enrichmentJob = ref<EnrichmentJobStatusResponse | null>(null)
  const enrichmentEnabled = ref(false)

  // Polling timers
  let syncPollTimer: ReturnType<typeof setInterval> | null = null
  let enrichPollTimer: ReturnType<typeof setInterval> | null = null

  // Sync actions
  async function loadSyncSources() {
    syncLoading.value = true
    try {
      // Config reload is best-effort — the endpoint may not be available during init
      await api.post('/config/reload').catch(() => {})
      const [sources, gog, epic] = await Promise.all([
        api.get<SyncSourceResponse[]>('/sync/sources'),
        api.get<{ enabled: boolean; connected: boolean; auth_url?: string }>('/gog/status').catch(() => null),
        api.get<{ enabled: boolean; connected: boolean; auth_url?: string }>('/epic/status').catch(() => null),
      ])
      syncSources.value = sources
      if (gog) {
        gogStatus.value = { authUrl: gog.auth_url || null, connected: gog.connected }
      }
      if (epic) {
        epicStatus.value = { authUrl: epic.auth_url || null, connected: epic.connected }
      }
    } catch {
      syncSources.value = []
    } finally {
      syncLoading.value = false
    }
  }

  async function triggerSync(source: string) {
    syncMessage.value = `Starting sync for ${source}...`
    syncStatus.value = 'running'
    syncingSource.value = source
    try {
      const data = await api.post<{ message: string }>('/update', { source })
      syncMessage.value = data.message
      startSyncPolling()
    } catch (err) {
      console.error('Sync trigger failed:', err)
      syncMessage.value = err instanceof ApiError
        ? `Error: server returned ${err.status}`
        : 'Error: unexpected failure — check the console'
      syncStatus.value = 'failed'
      syncingSource.value = null
    }
  }

  async function checkSyncStatus() {
    try {
      const data = await api.get<SyncStatusResponse>('/sync/status')
      syncJob.value = data.job || null

      if (data.status === 'running' && data.job) {
        syncStatus.value = 'running'
        // Restore syncingSource from server state (e.g. page reload mid-sync).
        // Use job.source (the original trigger, e.g. "all"), NOT job.current_source
        // (which tracks the individual source currently processing within an "all" sync).
        if (!syncingSource.value) {
          syncingSource.value = data.job.source
        }
        syncMessage.value = buildProgressMessage(data.job)
        if (!syncPollTimer) startSyncPolling()
      } else if (data.status === 'completed' && data.job) {
        syncStatus.value = 'completed'
        syncingSource.value = null
        let msg = `Completed: ${data.job.items_processed} items synced`
        if (data.job.error_count > 0) msg += ` (${data.job.error_count} errors)`
        syncMessage.value = msg
        stopSyncPolling()
        loadEnrichmentStats()
      } else if (data.status === 'failed' && data.job) {
        syncStatus.value = 'failed'
        syncingSource.value = null
        syncMessage.value = `Failed: ${data.job.error_message || 'Unknown error'}`
        stopSyncPolling()
      } else {
        syncStatus.value = 'idle'
        syncingSource.value = null
        syncMessage.value = ''
        stopSyncPolling()
      }
    } catch {
      // Ignore polling errors
    }
  }

  function startSyncPolling() {
    if (syncPollTimer) return
    syncPollTimer = setInterval(checkSyncStatus, 2000)
  }

  function stopSyncPolling() {
    if (syncPollTimer) {
      clearInterval(syncPollTimer)
      syncPollTimer = null
    }
  }

  function buildProgressMessage(job: SyncJobResponse): string {
    const parts: string[] = []
    if (job.total_items != null && job.total_items > 0) {
      let progress = `${job.items_processed}/${job.total_items}`
      if (job.progress_percent != null) progress += ` (${job.progress_percent}%)`
      parts.push(progress)
    } else {
      parts.push(`${job.items_processed} items so far`)
    }
    const source = job.current_source || job.source
    const item = job.current_item ? truncate(job.current_item, 50) : '...'
    parts.push(`- Syncing ${source}: ${item}`)
    return parts.join(' ')
  }

  // GOG/Epic auth
  async function submitGogCode(codeOrUrl: string) {
    gogConnectMessage.value = 'Connecting to GOG...'
    try {
      const data = await api.post<{ message: string }>('/gog/exchange', { code_or_url: codeOrUrl })
      gogConnectMessage.value = data.message
      setTimeout(() => loadSyncSources(), 1500)
    } catch (err) {
      console.error('GOG connect failed:', err)
      gogConnectMessage.value = err instanceof ApiError
        ? `Error: server returned ${err.status}`
        : 'Error: connection failed'
    }
  }

  async function submitEpicCode(codeOrJson: string) {
    epicConnectMessage.value = 'Connecting to Epic Games...'
    try {
      const data = await api.post<{ message: string }>('/epic/exchange', { code_or_json: codeOrJson })
      epicConnectMessage.value = data.message
      setTimeout(() => loadSyncSources(), 1500)
    } catch (err) {
      console.error('Epic connect failed:', err)
      epicConnectMessage.value = err instanceof ApiError
        ? `Error: server returned ${err.status}`
        : 'Error: connection failed'
    }
  }

  // Enrichment actions
  async function loadEnrichmentStats() {
    const app = useAppStore()
    try {
      const stats = await api.get<EnrichmentStatsResponse>('/enrichment/stats', {
        user_id: app.currentUserId,
      })
      enrichmentStats.value = stats
      enrichmentEnabled.value = stats.enabled
    } catch {
      enrichmentStats.value = null
    }
  }

  async function startEnrichment(contentType?: string, retryNotFound = false) {
    const app = useAppStore()
    try {
      await api.post('/enrichment/start', {
        content_type: contentType || undefined,
        user_id: app.currentUserId,
        retry_not_found: retryNotFound,
      })
      startEnrichmentPolling()
    } catch {
      // Ignore
    }
  }

  async function stopEnrichment() {
    try {
      await api.post('/enrichment/stop')
      stopEnrichmentPolling()
      await checkEnrichmentStatus()
    } catch {
      // Ignore
    }
  }

  async function resetEnrichment(contentType?: string) {
    const app = useAppStore()
    try {
      await api.post('/enrichment/reset', {
        content_type: contentType || undefined,
        user_id: app.currentUserId,
      })
      startEnrichmentPolling()
    } catch {
      // Ignore
    }
  }

  async function checkEnrichmentStatus() {
    try {
      const status = await api.get<EnrichmentJobStatusResponse>('/enrichment/status')
      enrichmentJob.value = status
      if (status.running) {
        if (!enrichPollTimer) startEnrichmentPolling()
      } else {
        stopEnrichmentPolling()
        if (status.completed) {
          await loadEnrichmentStats()
        }
      }
    } catch {
      enrichmentJob.value = null
    }
  }

  function startEnrichmentPolling() {
    if (enrichPollTimer) return
    enrichPollTimer = setInterval(checkEnrichmentStatus, 3000)
  }

  function stopEnrichmentPolling() {
    if (enrichPollTimer) {
      clearInterval(enrichPollTimer)
      enrichPollTimer = null
    }
  }

  function cleanup() {
    stopSyncPolling()
    stopEnrichmentPolling()
  }

  return {
    // State
    syncSources,
    syncStatus,
    syncingSource,
    syncJob,
    syncMessage,
    syncLoading,
    gogStatus,
    epicStatus,
    gogConnectMessage,
    epicConnectMessage,
    enrichmentStats,
    enrichmentJob,
    enrichmentEnabled,
    // Actions
    loadSyncSources,
    triggerSync,
    checkSyncStatus,
    submitGogCode,
    submitEpicCode,
    loadEnrichmentStats,
    startEnrichment,
    stopEnrichment,
    resetEnrichment,
    checkEnrichmentStatus,
    cleanup,
  }
})

