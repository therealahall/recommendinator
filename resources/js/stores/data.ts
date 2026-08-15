import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { useApi, ApiError } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { truncate } from '@/utils/format'
import type {
  SyncSourceResponse,
  SyncStatusResponse,
  SyncErrorResponse,
  SyncJobResponse,
  EnrichmentStatsResponse,
  EnrichmentJobStatusResponse,
  SourceSchemaResponse,
  SourceConfigResponse,
  SourceMigrationResponse,
  PluginImportErrorResponse,
  PluginInfoResponse,
  PluginListResponse,
  SourceCreateRequest,
  OAuthStatusResponse,
  TraktDeviceFlowResponse,
  TraktPollResponse,
} from '@/types/api'

const ALL_SOURCES_LABEL = 'All Sources'

/** The status/connect/disconnect route prefix for each OAuth-backed plugin. */
const OAUTH_ROUTE_BY_PLUGIN: Record<string, string> = {
  gog: 'gog',
  epic_games: 'epic',
  trakt: 'trakt',
}

export interface OAuthStatus {
  enabled: boolean
  connected: boolean
  authUrl: string | null
}

// Frozen: it is handed to every caller asking about a source nobody has loaded.
const DISCONNECTED: OAuthStatus = Object.freeze({
  enabled: false,
  connected: false,
  authUrl: null,
})

export const useDataStore = defineStore('data', () => {
  const api = useApi()

  // Sync state — multi-job after issue #45.
  const syncSources = ref<SyncSourceResponse[]>([])
  const syncJobs = ref<SyncJobResponse[]>([])
  // Aggregate banner status: 'running' if any job running, else last terminal.
  const syncStatus = ref<'idle' | 'running' | 'completed' | 'failed'>('idle')
  const syncMessage = ref('')
  const syncLoading = ref(false)
  const syncSourcesError = ref('')
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

  /** The umbrella run, for a source it resolved to sync. The server fixes
   *  that list at trigger time, so a source enabled mid-run is not in it. */
  function umbrellaJobFor(label: string): SyncJobResponse | null {
    const umbrella = jobForLabel(ALL_SOURCES_LABEL)
    if (!umbrella) return null
    return umbrella.sources.some((entry) => entry.source === label)
      ? umbrella
      : null
  }

  /** Terminal jobs are retained, so rendering the older of the two is how a
   *  message from the newer run ends up displayed nowhere. */
  function currentJobForLabel(label: string): SyncJobResponse | null {
    const own = jobForLabel(label)
    if (label === ALL_SOURCES_LABEL) return own
    const umbrella = umbrellaJobFor(label)
    if (!own || !umbrella) return own || umbrella
    // Both are ISO-8601 off the same clock, so ordering them as text orders
    // them in time — and no parse can trip over Python's microseconds.
    return (umbrella.started_at ?? '') > (own.started_at ?? '') ? umbrella : own
  }

  function jobForSourceId(sourceId: string): SyncJobResponse | null {
    if (sourceId === 'all') return jobForLabel(ALL_SOURCES_LABEL)
    const display = syncSources.value.find((s) => s.id === sourceId)?.display_name
    return display ? currentJobForLabel(display) : null
  }

  function isSourceIdSyncing(sourceId: string): boolean {
    if (sourceId === 'all') return isLabelRunning(ALL_SOURCES_LABEL)
    const display = syncSources.value.find((s) => s.id === sourceId)?.display_name
    if (!display) return false
    return isLabelRunning(display) || umbrellaJobFor(display)?.status === 'running'
  }

  // Auth state, per source id — a token belongs to the source it was obtained
  // for, and two sources can run the same OAuth plugin.
  const oauthStatus = ref<Record<string, OAuthStatus>>({})
  const oauthMessages = ref<Record<string, string>>({})
  // Per source, never one counter: two sources rechecking at once are unrelated
  // reads and must not cancel each other.
  const oauthStatusGeneration: Record<string, number> = {}

  function oauthStatusFor(sourceId: string): OAuthStatus {
    return oauthStatus.value[sourceId] ?? DISCONNECTED
  }

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
    syncSourcesError.value = ''
    try {
      // Config reload is best-effort — the endpoint may not be available during init
      await api.post('/config/reload').catch(() => {})
      syncSources.value = await api.get<SyncSourceResponse[]>('/sync/sources')
    } catch (err) {
      // The list is left alone: emptying it makes a failed read look like a
      // configuration the user is told to go and fix.
      syncSourcesError.value = err instanceof Error ? err.message : 'Unknown error'
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
          const errors = visibleErrors()
          let msg = `Completed: ${buildCountsSummary(syncJobs.value)}`
          if (errors.length > 0) msg += ` — ${describeErrors(errors)}`
          syncMessage.value = msg
        }
        stopSyncPolling()
        // A completed sync may have auto-triggered enrichment server-side
        // (enrichment.auto_enrich_on_sync). checkEnrichmentStatus refreshes
        // the stats AND starts polling when a job is running, so the data
        // view live-updates enrichment progress without a manual reload —
        // a one-shot loadEnrichmentStats would miss the running job.
        checkEnrichmentStatus()
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

  function visibleErrors(): SyncErrorResponse[] {
    return syncJobs.value.flatMap((job) =>
      job.errors.filter((error) => currentJobForLabel(error.source) === job),
    )
  }

  /** What the finished run did, in the words the `update` command uses. A
   *  count of items touched cannot tell a first import from a second run of
   *  it, which is the question the banner is read for. */
  function buildCountsSummary(jobs: SyncJobResponse[]): string {
    const sum = (pick: (job: SyncJobResponse) => number): number =>
      jobs.reduce((total, job) => total + pick(job), 0)
    const saved = sum((job) => job.items_processed)
    const found = sum((job) => job.total_items || 0)
    return (
      `${saved} of ${found} items saved (${sum((job) => job.items_added)} added, ` +
      `${sum((job) => job.items_updated)} updated, ` +
      `${sum((job) => job.items_unchanged)} unchanged)`
    )
  }

  // The first message in full, not a count of them: the text is what names
  // the setting to change, and the rest are one row away in the accordion.
  function describeErrors(errors: SyncErrorResponse[]): string {
    const [first, ...rest] = errors
    const more = rest.length > 0 ? ` (+${rest.length} more)` : ''
    return `${first.source}: ${first.message}${more}`
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

    // Errors land on the job as each source finishes, so a run that is still
    // going has a count worth showing: the accordion rows only render them
    // once the whole run is over.
    const failures = running.reduce((sum, j) => sum + j.errors.length, 0)
    const errorNote = failures > 0 ? ` — ${failures} error(s) so far` : ''

    if (running.length === 1) {
      const job = running[0]
      const item = job.current_item ? truncate(job.current_item, 50) : '...'
      return `${summary} - Syncing ${job.source}: ${item}${errorNote}`
    }
    return `${summary} - Syncing ${running.length} sources in parallel${errorNote}`
  }

  // OAuth connect flows. Every call names the source being connected: the
  // token is stored under that id and read back from it at sync time.
  async function loadOAuthStatus(sourceId: string, plugin: string): Promise<void> {
    const route = OAUTH_ROUTE_BY_PLUGIN[plugin]
    if (!route) return
    const generation = (oauthStatusGeneration[sourceId] ?? 0) + 1
    oauthStatusGeneration[sourceId] = generation
    const status = await api.get<OAuthStatusResponse>(`/${route}/status`, {
      source_id: sourceId,
    })
    // Responses need not arrive in the order they were asked for. The older one
    // landing last leaves a gate the UI has already moved past, and nothing
    // after it to correct the dead button that read leaves behind.
    if (generation !== oauthStatusGeneration[sourceId]) return
    oauthStatus.value = {
      ...oauthStatus.value,
      [sourceId]: {
        enabled: status.enabled,
        connected: status.connected,
        authUrl: status.auth_url || null,
      },
    }
  }

  function setOAuthMessage(sourceId: string, message: string): void {
    oauthMessages.value = { ...oauthMessages.value, [sourceId]: message }
  }

  function oauthErrorMessage(err: unknown, fallback: string): string {
    // ApiError.message is the server's own ``detail`` when it sent one, which
    // is the only part of a refusal that tells the user what to do about it
    // ("GOG is not enabled for that source.").
    return err instanceof ApiError ? `Error: ${err.message}` : fallback
  }

  async function submitOAuthCode(
    sourceId: string,
    plugin: string,
    body: Record<string, string>,
    pendingMessage: string,
  ): Promise<void> {
    setOAuthMessage(sourceId, pendingMessage)
    try {
      const data = await api.post<{ message: string }>(
        `/${OAUTH_ROUTE_BY_PLUGIN[plugin]}/exchange`,
        body,
        { source_id: sourceId },
      )
      setOAuthMessage(sourceId, data.message)
    } catch (err) {
      console.error('OAuth connect failed:', err)
      setOAuthMessage(sourceId, oauthErrorMessage(err, 'Error: connection failed'))
      return
    }
    // Rejects to the caller: the token is stored by now, so a stale panel would
    // offer Connect for an account that is connected. The confirmation above
    // stands either way — this is not a failed connect.
    await loadOAuthStatus(sourceId, plugin)
  }

  function submitGogCode(sourceId: string, codeOrUrl: string) {
    return submitOAuthCode(
      sourceId,
      'gog',
      { code_or_url: codeOrUrl },
      'Connecting to GOG...',
    )
  }

  function submitEpicCode(sourceId: string, codeOrJson: string) {
    return submitOAuthCode(
      sourceId,
      'epic_games',
      { code_or_json: codeOrJson },
      'Connecting to Epic Games...',
    )
  }

  async function disconnectOAuth(
    sourceId: string,
    plugin: string,
    pendingMessage: string,
  ): Promise<void> {
    setOAuthMessage(sourceId, pendingMessage)
    try {
      // The DELETE runs the credential delete synchronously and only returns
      // 200 once the row is gone, so the status re-read below sees the result.
      await api.delete(`/${OAUTH_ROUTE_BY_PLUGIN[plugin]}/token`, {
        source_id: sourceId,
      })
      setOAuthMessage(sourceId, 'Disconnected. You can reconnect below.')
    } catch (err) {
      console.error('OAuth disconnect failed:', err)
      setOAuthMessage(sourceId, oauthErrorMessage(err, 'Error: disconnect failed'))
      return
    }
    // Rejects to the caller: the credential is gone by now, so a stale panel
    // would keep claiming the account is connected, next to the message saying
    // it was disconnected.
    await loadOAuthStatus(sourceId, plugin)
  }

  function disconnectGog(sourceId: string) {
    return disconnectOAuth(sourceId, 'gog', 'Disconnecting GOG...')
  }

  function disconnectEpic(sourceId: string) {
    return disconnectOAuth(sourceId, 'epic_games', 'Disconnecting Epic Games...')
  }

  // Trakt device-code auth
  async function startTraktFlow(sourceId: string): Promise<TraktDeviceFlowResponse> {
    return api.post<TraktDeviceFlowResponse>('/trakt/start-device-flow', undefined, {
      source_id: sourceId,
    })
  }

  async function pollTraktApproval(
    sourceId: string,
    deviceCode: string,
  ): Promise<TraktPollResponse> {
    const result = await api.post<TraktPollResponse>(
      '/trakt/poll-device-approval',
      { device_code: deviceCode },
      { source_id: sourceId },
    )
    if (result.connected) {
      // The confirmation belongs to the panel, not to the device-code flow:
      // the status re-read below unmounts that flow, taking its live region
      // with it before anything could be announced from there.
      setOAuthMessage(sourceId, result.message || 'Trakt account connected.')
      try {
        await loadOAuthStatus(sourceId, 'trakt')
      } catch (err) {
        // Swallowed, unlike the connect and disconnect re-reads: this one is
        // awaited inside the poll loop, whose own catch reports the connect
        // itself as failed. The flow reads the flag back and says the status
        // could not be re-read.
        console.error('Trakt status re-read failed:', err)
      }
    }
    return result
  }

  function disconnectTrakt(sourceId: string) {
    return disconnectOAuth(sourceId, 'trakt', 'Disconnecting Trakt...')
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
        await loadEnrichmentStats()
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
  const pluginImportErrors = ref<PluginImportErrorResponse[]>([])

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
    const view = await api.get<PluginListResponse>('/plugins')
    availablePlugins.value = view.plugins
    pluginImportErrors.value = view.import_errors
    return view.plugins
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
    const remainingStatus = { ...oauthStatus.value }
    delete remainingStatus[sourceId]
    oauthStatus.value = remainingStatus
    // A read still in flight would land after this prune and re-seed a deleted
    // id. The counter itself stays behind: monotonic per id, it keeps a source
    // recreated under that id outrunning its predecessor's reads.
    oauthStatusGeneration[sourceId] = (oauthStatusGeneration[sourceId] ?? 0) + 1
    const remainingMessages = { ...oauthMessages.value }
    delete remainingMessages[sourceId]
    oauthMessages.value = remainingMessages
  }

  return {
    // State
    syncSources,
    syncStatus,
    syncJobs,
    syncMessage,
    syncLoading,
    syncSourcesError,
    // Helpers
    isSourceIdSyncing,
    jobForSourceId,
    oauthStatus,
    oauthMessages,
    oauthStatusFor,
    enrichmentStats,
    enrichmentJob,
    enrichmentEnabled,
    sourceSchemas,
    sourceConfigs,
    availablePlugins,
    pluginImportErrors,
    // Actions
    loadSyncSources,
    triggerSync,
    checkSyncStatus,
    loadOAuthStatus,
    setOAuthMessage,
    submitGogCode,
    submitEpicCode,
    disconnectGog,
    disconnectEpic,
    startTraktFlow,
    pollTraktApproval,
    disconnectTrakt,
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

