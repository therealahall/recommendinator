import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
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
  SourceSchemaResponse,
  SourceConfigResponse,
  SourceMigrationResponse,
  PluginInfoResponse,
  SourceCreateRequest,
} from '@/types/api'

const ALL_SOURCES_LABEL = 'All Sources'

export const useDataStore = defineStore('data', () => {
  const api = useApi()

  // Sync state — multi-job after issue #45.
  const syncSources = ref<SyncSourceResponse[]>([])
  const syncJobs = ref<SyncJobResponse[]>([])
  // Aggregate banner status: 'running' if any job running, else last terminal.
  const syncStatus = ref<'idle' | 'running' | 'completed' | 'failed'>('idle')
  const syncMessage = ref('')
  const syncLoading = ref(false)
  // Optimistic set of in-flight source labels — populated immediately on
  // triggerSync so the per-accordion Sync button switches to "Syncing…"
  // without waiting for the next /sync/status poll.
  const optimisticTriggers = ref<Set<string>>(new Set())

  // Map of currently RUNNING jobs by source label, derived from /sync/status.
  const jobsByLabel = computed<Record<string, SyncJobResponse>>(() => {
    const map: Record<string, SyncJobResponse> = {}
    for (const job of syncJobs.value) map[job.source] = job
    return map
  })

  function isLabelRunning(label: string): boolean {
    if (optimisticTriggers.value.has(label)) return true
    const job = jobsByLabel.value[label]
    return job?.status === 'running'
  }

  function jobForLabel(label: string): SyncJobResponse | null {
    return jobsByLabel.value[label] || null
  }

  function jobForSourceId(sourceId: string): SyncJobResponse | null {
    if (sourceId === 'all') return jobForLabel(ALL_SOURCES_LABEL)
    const display = syncSources.value.find((s) => s.id === sourceId)?.display_name
    return display ? jobForLabel(display) : null
  }

  function isSourceIdSyncing(sourceId: string): boolean {
    if (sourceId === 'all') return isLabelRunning(ALL_SOURCES_LABEL)
    const display = syncSources.value.find((s) => s.id === sourceId)?.display_name
    return display ? isLabelRunning(display) : false
  }

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

  function _labelForSourceId(sourceId: string): string {
    if (sourceId === 'all') return ALL_SOURCES_LABEL
    const found = syncSources.value.find((s) => s.id === sourceId)
    if (!found) {
      // Fallback to the raw ID so triggerSync can still post the request,
      // but warn loudly: a missing entry in syncSources usually means the
      // store has not loaded yet or the caller passed a stale ID.
      console.warn(
        `triggerSync: no source with id="${sourceId}" in syncSources; ` +
          'using the raw ID as the job label fallback.',
      )
      return sourceId
    }
    return found.display_name
  }

  async function triggerSync(sourceId: string) {
    const label = _labelForSourceId(sourceId)
    syncMessage.value = `Starting sync for ${label}...`
    syncStatus.value = 'running'
    optimisticTriggers.value = new Set([...optimisticTriggers.value, label])
    try {
      const data = await api.post<{ message: string }>('/update', {
        source: sourceId,
      })
      syncMessage.value = data.message
      startSyncPolling()
    } catch (err) {
      console.error('Sync trigger failed:', err)
      syncMessage.value =
        err instanceof ApiError
          ? `Error: server returned ${err.status}`
          : 'Error: unexpected failure — check the console'
      const next = new Set(optimisticTriggers.value)
      next.delete(label)
      optimisticTriggers.value = next
      if (next.size === 0) syncStatus.value = 'failed'
    }
  }

  async function checkSyncStatus() {
    try {
      const data = await api.get<SyncStatusResponse>('/sync/status')
      syncJobs.value = data.jobs || []

      // Drop optimistic flags whose labels the server has now ack'd —
      // start_sync transitions the job to RUNNING before returning, so
      // any label present in syncJobs is also reflected in the server's
      // authoritative state and no longer needs the optimistic shadow.
      const seen = new Set(syncJobs.value.map((j) => j.source))
      const next = new Set<string>()
      for (const label of optimisticTriggers.value) {
        if (!seen.has(label)) next.add(label)
      }
      optimisticTriggers.value = next

      const runningJobs = syncJobs.value.filter((j) => j.status === 'running')
      const anyRunning = runningJobs.length > 0 || next.size > 0

      if (anyRunning) {
        syncStatus.value = 'running'
        syncMessage.value = buildRunningMessage(runningJobs)
        if (!syncPollTimer) startSyncPolling()
      } else if (syncJobs.value.length > 0) {
        const failedJobs = syncJobs.value.filter((j) => j.status === 'failed')
        if (failedJobs.length > 0) {
          syncStatus.value = 'failed'
          const first = failedJobs[0]
          syncMessage.value = `Failed (${first.source}): ${
            first.error_message || 'Unknown error'
          }`
        } else {
          syncStatus.value = 'completed'
          const totalItems = syncJobs.value.reduce(
            (sum, j) => sum + j.items_processed,
            0,
          )
          const totalErrors = syncJobs.value.reduce(
            (sum, j) => sum + j.error_count,
            0,
          )
          let msg = `Completed: ${totalItems} items synced`
          if (totalErrors > 0) msg += ` (${totalErrors} errors)`
          syncMessage.value = msg
        }
        stopSyncPolling()
        loadEnrichmentStats()
      } else {
        syncStatus.value = 'idle'
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

  function buildRunningMessage(running: SyncJobResponse[]): string {
    if (running.length === 0) return ''

    const totalProcessed = running.reduce(
      (sum, j) => sum + j.items_processed,
      0,
    )
    const totalKnown = running.reduce(
      (sum, j) => sum + (j.total_items || 0),
      0,
    )

    let summary: string
    if (totalKnown > 0) {
      const pct = Math.min(100, Math.floor((totalProcessed * 100) / totalKnown))
      summary = `${totalProcessed}/${totalKnown} (${pct}%)`
    } else {
      summary = `${totalProcessed} items so far`
    }

    if (running.length === 1) {
      const job = running[0]
      const item = job.current_item ? truncate(job.current_item, 50) : '...'
      return `${summary} - Syncing ${job.source}: ${item}`
    }
    return `${summary} - Syncing ${running.length} sources in parallel`
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

  async function disconnectGog() {
    gogConnectMessage.value = 'Disconnecting GOG...'
    try {
      // DELETE /api/gog/token runs the credential delete synchronously and
      // only returns 200 once the row is gone, so loadSyncSources reads
      // the post-delete state immediately — no setTimeout needed.
      await api.delete('/gog/token')
      gogConnectMessage.value = 'Disconnected. You can reconnect below.'
      await loadSyncSources()
    } catch (err) {
      console.error('GOG disconnect failed:', err)
      gogConnectMessage.value = err instanceof ApiError
        ? `Error: server returned ${err.status}`
        : 'Error: disconnect failed'
    }
  }

  async function disconnectEpic() {
    epicConnectMessage.value = 'Disconnecting Epic Games...'
    try {
      // DELETE /api/epic/token is synchronous (see disconnectGog comment).
      await api.delete('/epic/token')
      epicConnectMessage.value = 'Disconnected. You can reconnect below.'
      await loadSyncSources()
    } catch (err) {
      console.error('Epic disconnect failed:', err)
      epicConnectMessage.value = err instanceof ApiError
        ? `Error: server returned ${err.status}`
        : 'Error: disconnect failed'
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

  // Per-source config flows.

  const sourceSchemas = ref<Record<string, SourceSchemaResponse>>({})
  const sourceConfigs = ref<Record<string, SourceConfigResponse>>({})
  const availablePlugins = ref<PluginInfoResponse[]>([])

  async function loadSourceSchema(sourceId: string): Promise<SourceSchemaResponse> {
    const schema = await api.get<SourceSchemaResponse>(
      `/sync/sources/${encodeURIComponent(sourceId)}/schema`,
    )
    sourceSchemas.value = { ...sourceSchemas.value, [sourceId]: schema }
    return schema
  }

  async function loadSourceConfig(sourceId: string): Promise<SourceConfigResponse> {
    const config = await api.get<SourceConfigResponse>(
      `/sync/sources/${encodeURIComponent(sourceId)}/config`,
    )
    sourceConfigs.value = { ...sourceConfigs.value, [sourceId]: config }
    return config
  }

  async function migrateSource(sourceId: string): Promise<SourceMigrationResponse> {
    const migration = await api.post<SourceMigrationResponse>(
      `/sync/sources/${encodeURIComponent(sourceId)}/migrate`,
    )
    await loadSourceConfig(sourceId)
    return migration
  }

  async function updateSourceConfig(
    sourceId: string,
    values: Record<string, unknown>,
  ): Promise<void> {
    await api.put(
      `/sync/sources/${encodeURIComponent(sourceId)}/config`,
      { values },
    )
    await loadSourceConfig(sourceId)
  }

  async function setSourceSecret(
    sourceId: string,
    key: string,
    value: string,
  ): Promise<void> {
    await api.put(
      `/sync/sources/${encodeURIComponent(sourceId)}/secret/${encodeURIComponent(key)}`,
      { value },
    )
    await loadSourceConfig(sourceId)
  }

  async function clearSourceSecret(sourceId: string, key: string): Promise<void> {
    await api.delete(
      `/sync/sources/${encodeURIComponent(sourceId)}/secret/${encodeURIComponent(key)}`,
    )
    await loadSourceConfig(sourceId)
  }

  async function setSourceEnabled(
    sourceId: string,
    enabled: boolean,
  ): Promise<void> {
    const updated = await api.put<SourceConfigResponse>(
      `/sync/sources/${encodeURIComponent(sourceId)}/enabled`,
      { enabled },
    )
    sourceConfigs.value = { ...sourceConfigs.value, [sourceId]: updated }
    // Mirror the enabled flag onto the listing entry so the accordion's
    // collapsed-state UI (Disabled badge, Sync button) updates immediately
    // without waiting for a syncSources reload.
    syncSources.value = syncSources.value.map((source) =>
      source.id === sourceId ? { ...source, enabled } : source,
    )
  }

  async function loadAvailablePlugins(): Promise<PluginInfoResponse[]> {
    const plugins = await api.get<PluginInfoResponse[]>('/plugins')
    availablePlugins.value = plugins
    return plugins
  }

  async function createSource(
    payload: SourceCreateRequest,
  ): Promise<SourceConfigResponse> {
    const created = await api.post<SourceConfigResponse>(
      '/sync/sources',
      payload,
    )
    sourceConfigs.value = { ...sourceConfigs.value, [created.source_id]: created }
    // Refresh the listing from the server so the new entry's display_name
    // matches ``humanize_source_id`` (the server-side canonical form, which
    // applies acronym capitalisation we'd diverge from if synthesised here).
    await loadSyncSources()
    return created
  }

  async function deleteSource(sourceId: string): Promise<void> {
    await api.delete(`/sync/sources/${encodeURIComponent(sourceId)}`)
    syncSources.value = syncSources.value.filter((s) => s.id !== sourceId)
    const remainingConfigs = { ...sourceConfigs.value }
    delete remainingConfigs[sourceId]
    sourceConfigs.value = remainingConfigs
    const remainingSchemas = { ...sourceSchemas.value }
    delete remainingSchemas[sourceId]
    sourceSchemas.value = remainingSchemas
  }

  return {
    // State
    syncSources,
    syncStatus,
    syncJobs,
    syncMessage,
    syncLoading,
    // Helpers
    isSourceIdSyncing,
    jobForSourceId,
    gogStatus,
    epicStatus,
    gogConnectMessage,
    epicConnectMessage,
    enrichmentStats,
    enrichmentJob,
    enrichmentEnabled,
    sourceSchemas,
    sourceConfigs,
    availablePlugins,
    // Actions
    loadSyncSources,
    triggerSync,
    checkSyncStatus,
    submitGogCode,
    submitEpicCode,
    disconnectGog,
    disconnectEpic,
    loadEnrichmentStats,
    startEnrichment,
    stopEnrichment,
    resetEnrichment,
    checkEnrichmentStatus,
    loadSourceSchema,
    loadSourceConfig,
    migrateSource,
    updateSourceConfig,
    setSourceSecret,
    clearSourceSecret,
    setSourceEnabled,
    loadAvailablePlugins,
    createSource,
    deleteSource,
    cleanup,
  }
})

