import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'
import { ApiError } from '@/composables/useApi'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

// Only the transport is faked. ApiError is the real class so the store is
// tested against the message the app actually gets, including the server's
// ``detail`` — a hand-rolled stand-in would hide that it carries one.
vi.mock('@/composables/useApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useApi')>()),
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

describe('useDataStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockGet.mockReset()
    mockPost.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function steamSource() {
    return {
      id: 'steam',
      display_name: 'Steam',
      plugin_display_name: 'Steam',
      enabled: true,
    }
  }

  /** A listing entry as GET /sync/sources sends it, schedule fields included. */
  function listedSource(id: string, displayName: string) {
    return {
      id,
      display_name: displayName,
      plugin_display_name: 'Fake File',
      enabled: true,
      plugin_not_loaded: null,
      sync_interval: 'off',
      sync_interval_default: 'off',
      last_run_at: null,
      last_run_status: null,
      next_run_at: null,
    }
  }

  /** The per-source slots an umbrella run carries — one per source it
   *  resolved to sync, which is how a row knows the run includes it. */
  function ranSources(...names: string[]) {
    return names.map((source) => ({
      source,
      items_processed: 0,
      total_items: null,
      current_item: null,
      progress_percent: null,
    }))
  }

  it('triggerSync marks the source label as syncing', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for Steam' })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('running')
    expect(store.syncMessage).toBe('Sync started for Steam')
    expect(store.isSourceIdSyncing('steam')).toBe(true)
    expect(store.isSourceIdSyncing('goodreads')).toBe(false)
  })

  it('triggerSync clears the optimistic trigger and starts no polling on API error', async () => {
    // Covers the DB-only-source regression too: the /update backend now
    // answers 4xx (not a 200 "message") for a disabled/unconfigured source, and
    // triggerSync's catch does not branch on status, so any error status hits
    // this path. The button must re-enable AND no poll timer may start (there
    // is no SyncJob to end it — a leaked timer would leave the button spinning).
    mockPost.mockRejectedValue(new ApiError(503, 'Service Unavailable'))

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.syncMessage).toContain('server returned 503')
    // No poll timer left running to spin against a non-existent job.
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGet.mock.calls.length).toBe(callsBefore)
  })

  it('triggerSync keeps the optimistic flag and starts polling on a successful start', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for Steam' })
    mockGet.mockResolvedValue({ status: 'idle', jobs: [] })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.isSourceIdSyncing('steam')).toBe(true)
    // Polling started: exactly one tick fires exactly one GET /sync/status.
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGet.mock.calls.length).toBe(callsBefore + 1)
    expect(mockGet).toHaveBeenLastCalledWith('/sync/status')

    store.cleanup()
  })

  it('checkSyncStatus matches the "all" source ID to the "All Sources" job', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'All Sources',
          status: 'running',
          items_processed: 4,
          total_items: 10,
          progress_percent: 40,
          current_source: 'Steam',
          current_item: 'Half-Life 2',
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.isSourceIdSyncing('all')).toBe(true)
    const job = store.jobForSourceId('all')
    expect(job?.source).toBe('All Sources')
    expect(job?.items_processed).toBe(4)
  })

  it('checkSyncStatus reports completed when no jobs are running', async () => {
    mockGet.mockResolvedValue({
      status: 'idle',
      jobs: [
        {
          source: 'Steam',
          status: 'completed',
          items_processed: 42,
          total_items: 42,
          items_added: 2,
          items_updated: 0,
          items_unchanged: 40,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('completed')
    // The whole point of the counts: 42 saved reads the same on a first
    // import and on a re-run that changed nothing.
    expect(store.syncMessage).toBe(
      'Completed (Steam): 42 of 42 items saved (2 added, 0 updated, 40 unchanged)',
    )
    expect(store.isSourceIdSyncing('steam')).toBe(false)
  })

  it('checkSyncStatus starts enrichment polling when a completed sync auto-triggered enrichment', async () => {
    // Reported in #59: after a sync that auto-triggers enrichment
    // (enrichment.auto_enrich_on_sync), the data view did not reflect that
    // enrichment was running and never live-updated — the user had to
    // navigate away and back. Root cause: the completed branch of
    // checkSyncStatus called loadEnrichmentStats() once (a one-shot refresh)
    // instead of checkEnrichmentStatus(), so the running job was never
    // detected and polling never started. Fix calls checkEnrichmentStatus()
    // which both refreshes stats and starts the 3s poll when running.
    const runningStatus = {
      running: true,
      completed: false,
      cancelled: false,
      items_processed: 5,
      items_enriched: 5,
      items_failed: 0,
      items_not_found: 0,
      total_items: 50,
      current_item: 'Half-Life 2',
      content_type: null,
      errors: [],
      elapsed_seconds: 2.0,
      progress_percent: 10,
    }
    const stats = {
      enabled: true,
      total: 50,
      enriched: 5,
      pending: 45,
      not_found: 0,
      failed: 0,
      by_provider: {},
      by_quality: {},
    }
    mockGet
      // checkSyncStatus -> GET /sync/status (completed, nothing running)
      .mockResolvedValueOnce({
        status: 'idle',
        jobs: [
          {
            source: 'Steam',
            status: 'completed',
            items_processed: 50,
            total_items: 50,
            errors: [],
            sources: [],
          },
        ],
      })
      // checkEnrichmentStatus -> GET /enrichment/status (job running)
      .mockResolvedValueOnce(runningStatus)
      // checkEnrichmentStatus -> GET /enrichment/stats (refresh while running)
      .mockResolvedValueOnce(stats)

    const store = useDataStore()
    await store.checkSyncStatus()
    // Let the checkEnrichmentStatus() chain (not awaited in the completed
    // branch) settle before asserting.
    await vi.advanceTimersByTimeAsync(0)

    expect(store.syncStatus).toBe('completed')
    expect(store.enrichmentJob).toEqual(runningStatus)
    expect(store.enrichmentStats).toEqual(stats)

    // Polling is live: a tick re-fetches status (running again) + stats,
    // and the refreshed values land in the store.
    const tickStatus = { ...runningStatus, items_processed: 30, items_enriched: 30, progress_percent: 60 }
    const tickStats = { ...stats, enriched: 30, pending: 20 }
    mockGet.mockResolvedValueOnce(tickStatus).mockResolvedValueOnce(tickStats)
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockGet).toHaveBeenCalledWith('/enrichment/status')
    expect(store.enrichmentJob).toEqual(tickStatus)
    expect(store.enrichmentStats).toEqual(tickStats)

    store.cleanup()
  })

  // A retained per-source job outlives the run that made it, so the row and
  // the banner could read different jobs — and a message the banner counted
  // was then shown on no row at all.
  describe('the banner and the rows read the same jobs', () => {
    function sonarrSource() {
      return {
        id: 'sonarr',
        display_name: 'Sonarr',
        plugin_display_name: 'Sonarr',
        enabled: true,
      }
    }

    it('drops a stale per-source job for the umbrella run that followed it', async () => {
      mockGet.mockResolvedValue({
        status: 'idle',
        jobs: [
          {
            source: 'All Sources',
            status: 'completed',
            started_at: '2026-08-13T10:05:00',
            items_processed: 20,
            errors: [
              { source: 'Sonarr', message: 'TLS verification failed' },
              { source: 'Steam', message: 'Rate limit exceeded' },
            ],
            sources: ranSources('Sonarr', 'Steam'),
          },
          {
            source: 'Steam',
            status: 'completed',
            started_at: '2026-08-13T10:00:00',
            items_processed: 30,
            errors: [],
            sources: [],
          },
        ],
      })

      const store = useDataStore()
      store.$patch({ syncSources: [steamSource(), sonarrSource()] })
      await store.checkSyncStatus()

      // The clean earlier Steam run kept the row, so Steam's message from the
      // umbrella run appeared nowhere while the banner counted it.
      expect(store.jobForSourceId('steam')?.source).toBe('All Sources')
      expect(store.jobForSourceId('sonarr')?.source).toBe('All Sources')
      expect(store.syncMessage).toContain('Sonarr: TLS verification failed')
      expect(store.syncMessage).toContain('(+1 more)')
    })

    it('leaves out an error the source has since re-synced past', async () => {
      mockGet.mockResolvedValue({
        status: 'idle',
        jobs: [
          {
            source: 'All Sources',
            status: 'completed',
            started_at: '2026-08-13T10:00:00',
            items_processed: 20,
            errors: [
              { source: 'Sonarr', message: 'TLS verification failed' },
              { source: 'Steam', message: 'Rate limit exceeded' },
            ],
            sources: ranSources('Sonarr', 'Steam'),
          },
          {
            source: 'Steam',
            status: 'completed',
            started_at: '2026-08-13T10:05:00',
            items_processed: 30,
            errors: [],
            sources: [],
          },
        ],
      })

      const store = useDataStore()
      store.$patch({ syncSources: [steamSource(), sonarrSource()] })
      await store.checkSyncStatus()

      expect(store.jobForSourceId('steam')?.source).toBe('Steam')
      expect(store.syncMessage).toContain('Sonarr: TLS verification failed')
      expect(store.syncMessage).not.toContain('more')
    })
  })

  it('checkSyncStatus surfaces a failed job before completed jobs', async () => {
    mockGet.mockResolvedValue({
      status: 'idle',
      jobs: [
        {
          source: 'Steam',
          status: 'failed',
          items_processed: 0,
          error_message: 'timeout',
          errors: [{ source: 'Steam', message: 'timeout' }],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('failed')
    expect(store.syncMessage).toContain('timeout')
    expect(store.syncMessage).toContain('Steam')
  })

  it('checkSyncStatus aggregates running jobs into a single banner', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Goodreads',
          status: 'running',
          items_processed: 5,
          total_items: 10,
          errors: [],
          sources: [],
        },
        {
          source: 'Steam',
          status: 'running',
          items_processed: 3,
          total_items: 20,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('8/30')
    expect(store.syncMessage).toContain('Syncing 2 sources in parallel')
  })

  describe('enrichment stats poll regression', () => {
    // Reported in #54: EnrichmentCard's "<enriched>/<total>" counter stayed
    // stale while a job was running — the user had to refresh the page to
    // see progress. Root cause: checkEnrichmentStatus polled
    // /enrichment/status every 3s but only refreshed /enrichment/stats on
    // job completion, so the top counter never updated mid-run. Fix
    // refreshes stats on every poll tick while running.
    let store: ReturnType<typeof useDataStore> | null = null

    afterEach(() => {
      store?.cleanup()
      store = null
    })

    it('checkEnrichmentStatus refreshes stats while job is running', async () => {
      const runningStatus = {
        running: true,
        completed: false,
        cancelled: false,
        items_processed: 25,
        items_enriched: 25,
        items_failed: 0,
        items_not_found: 0,
        total_items: 100,
        current_item: 'Some Game',
        content_type: null,
        errors: [],
        elapsed_seconds: 7.5,
        progress_percent: 25,
      }
      const updatedStats = {
        enabled: true,
        total: 100,
        enriched: 25,
        pending: 75,
        not_found: 0,
        failed: 0,
        by_provider: {},
        by_quality: {},
      }
      mockGet
        .mockResolvedValueOnce(runningStatus)
        .mockResolvedValueOnce(updatedStats)

      store = useDataStore()
      await store.checkEnrichmentStatus()

      expect(mockGet).toHaveBeenNthCalledWith(1, '/enrichment/status')
      expect(mockGet).toHaveBeenNthCalledWith(2, '/enrichment/stats', { user_id: 1 })
      expect(store.enrichmentStats).toEqual(updatedStats)
      expect(store.enrichmentJob).toEqual(runningStatus)
    })
  })

  /** Hold every status read open, in call order, so they can be answered
   *  out of it. */
  function pendingStatusReads(): Array<(status: unknown) => void> {
    const resolvers: Array<(status: unknown) => void> = []
    mockGet.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve)
        }),
    )
    return resolvers
  }

  // Every id below is deliberately not the plugin's own name: the token
  // belongs to the source being connected, not to the plugin.
  describe('oauth connect flows', () => {
    it('loadOAuthStatus asks for the named source and caches its flags', async () => {
      mockGet.mockResolvedValueOnce({
        enabled: true,
        connected: true,
        auth_url: 'https://gog.com/auth',
      })

      const store = useDataStore()
      await store.loadOAuthStatus('gog_work', 'gog')

      expect(mockGet).toHaveBeenCalledWith('/gog/status', { source_id: 'gog_work' })
      expect(store.oauthStatusFor('gog_work')).toEqual({
        enabled: true,
        connected: true,
        authUrl: 'https://gog.com/auth',
      })
      // A source nobody has loaded reads as disconnected, never as undefined.
      expect(store.oauthStatusFor('other').connected).toBe(false)
    })

    it('keeps the newer status when an overtaken read answers last', async () => {
      // Enabling a source and clearing a secret each recheck the same gate, and
      // the two reads can be in flight together.
      const answer = pendingStatusReads()

      const store = useDataStore()
      const overtaken = store.loadOAuthStatus('trakt_work', 'trakt')
      const latest = store.loadOAuthStatus('trakt_work', 'trakt')

      answer[1]({ enabled: true, connected: false, auth_url: null })
      await latest
      answer[0]({ enabled: false, connected: false, auth_url: null })
      await overtaken

      // Last write wins left the older read's gate in the store with nothing
      // after it to correct the dead Connect button that gate renders.
      expect(store.oauthStatusFor('trakt_work').enabled).toBe(true)
    })

    it('submitGogCode posts the code for that source and re-reads its status', async () => {
      mockPost.mockResolvedValueOnce({ message: 'GOG account connected!' })
      mockGet.mockResolvedValueOnce({ enabled: true, connected: true })

      const store = useDataStore()
      await store.submitGogCode('gog_work', 'auth-code')

      expect(mockPost).toHaveBeenCalledWith(
        '/gog/exchange',
        { code_or_url: 'auth-code' },
        { source_id: 'gog_work' },
      )
      expect(mockGet).toHaveBeenCalledWith('/gog/status', { source_id: 'gog_work' })
      expect(store.oauthMessages['gog_work']).toBe('GOG account connected!')
      expect(store.oauthStatusFor('gog_work').connected).toBe(true)
    })

    it('submitGogCode surfaces the refusal the server wrote for the user', async () => {
      mockPost.mockRejectedValueOnce(
        new ApiError(404, 'Not Found', {
          detail: 'GOG is not enabled for that source.',
        }),
      )

      const store = useDataStore()
      await store.submitGogCode('gog_work', 'auth-code')

      // The remedy text, not "server returned 404": the status code alone
      // tells the user nothing they can act on (WCAG 3.3.3).
      expect(store.oauthMessages['gog_work']).toBe(
        'Error: GOG is not enabled for that source.',
      )
    })

    it('submitGogCode keeps the confirmation and rejects when the re-read fails', async () => {
      mockPost.mockResolvedValueOnce({ message: 'GOG account connected!' })
      mockGet.mockRejectedValueOnce(new ApiError(503, 'Service Unavailable'))

      const store = useDataStore()

      // Swallowed, this left the panel offering Connect for an account that is
      // connected, with nothing anywhere saying the status is unknown.
      await expect(
        store.submitGogCode('gog_work', 'auth-code'),
      ).rejects.toBeInstanceOf(ApiError)
      // The token IS stored by the time the re-read runs, so reporting the
      // connect as failed would be a lie the user acts on.
      expect(store.oauthMessages['gog_work']).toBe('GOG account connected!')
    })

    it('disconnectGog deletes that source token and re-reads its status', async () => {
      mockDelete.mockResolvedValue({})
      mockGet.mockResolvedValueOnce({
        enabled: true,
        connected: false,
        auth_url: 'https://gog.com/auth',
      })

      const store = useDataStore()
      await store.disconnectGog('gog_work')

      expect(mockDelete).toHaveBeenCalledWith('/gog/token', { source_id: 'gog_work' })
      expect(mockGet).toHaveBeenCalledWith('/gog/status', { source_id: 'gog_work' })
      expect(store.oauthMessages['gog_work']).toBe(
        'Disconnected. You can reconnect below.',
      )
      expect(store.oauthStatusFor('gog_work').connected).toBe(false)
    })
  })

  describe('trakt device-code auth', () => {
    it('pollTraktApproval re-reads that source status on success', async () => {
      mockPost.mockResolvedValueOnce({ connected: true, message: 'Connected' })
      mockGet.mockResolvedValueOnce({ enabled: true, connected: true })

      const store = useDataStore()
      const result = await store.pollTraktApproval('trakt_work', 'dev-code')

      expect(result.connected).toBe(true)
      expect(mockGet).toHaveBeenCalledWith('/trakt/status', {
        source_id: 'trakt_work',
      })
      expect(store.oauthStatusFor('trakt_work').connected).toBe(true)
      // The device-code flow is unmounted by that very status flip, so the
      // confirmation has to reach the panel's own live region to be announced.
      expect(store.oauthMessages['trakt_work']).toBe('Connected')
    })

    it('loadOAuthStatus propagates the rejection without clobbering state', async () => {
      mockGet
        .mockResolvedValueOnce({ enabled: true, connected: true })
        .mockRejectedValueOnce(new ApiError(500, 'Internal Server Error'))

      const store = useDataStore()
      await store.loadOAuthStatus('trakt_work', 'trakt')

      await expect(
        store.loadOAuthStatus('trakt_work', 'trakt'),
      ).rejects.toBeInstanceOf(ApiError)
      // The cached status is untouched — the caller decides how to react.
      expect(store.oauthStatusFor('trakt_work')).toEqual({
        enabled: true,
        connected: true,
        authUrl: null,
      })
    })

    it('disconnectTrakt reports a refused disconnect instead of rejecting', async () => {
      mockGet.mockResolvedValueOnce({ enabled: true, connected: true })
      mockDelete.mockRejectedValueOnce(
        new ApiError(404, 'Not Found', { detail: 'No active Trakt connection found' }),
      )

      const store = useDataStore()
      await store.loadOAuthStatus('trakt_work', 'trakt')

      // The button that calls this drops the promise, so a rejection reaches
      // nobody: there is no global handler, and the panel would keep claiming
      // the account is connected with nothing said about the failure.
      await store.disconnectTrakt('trakt_work')

      expect(store.oauthMessages['trakt_work']).toBe(
        'Error: No active Trakt connection found',
      )
      // No re-read on failure, and the connected flag stays true so the UI
      // still shows the account as connected.
      expect(mockGet).toHaveBeenCalledTimes(1)
      expect(store.oauthStatusFor('trakt_work').connected).toBe(true)
    })
  })

  describe('source config flows', () => {
    it('migrateSource POSTs migrate and refreshes config', async () => {
      const migration = {
        source_id: 'steam',
        migrated_at: 'now',
        fields_migrated: ['vanity_url'],
        secrets_migrated: ['api_key'],
      }
      const cfg = {
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: { vanity_url: 'me' },
        secret_status: { api_key: true },
      }
      mockPost.mockResolvedValueOnce(migration)
      mockGet.mockResolvedValueOnce(cfg)

      const store = useDataStore()
      await store.migrateSource('steam')

      expect(mockPost).toHaveBeenCalledWith('/sync/sources/steam/migrate')
      expect(mockGet).toHaveBeenCalledWith('/sync/sources/steam/config')
      expect(store.sourceConfigs.steam).toEqual(cfg)
    })

    it('updateSourceConfig PUTs values and refreshes config', async () => {
      mockPut.mockResolvedValueOnce({})
      mockGet.mockResolvedValueOnce({
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: { vanity_url: 'new' },
        secret_status: {},
      })

      const store = useDataStore()
      await store.updateSourceConfig('steam', { vanity_url: 'new' })

      expect(mockPut).toHaveBeenCalledWith('/sync/sources/steam/config', {
        values: { vanity_url: 'new' },
      })
      expect(mockGet).toHaveBeenCalledWith('/sync/sources/steam/config')
      expect(store.sourceConfigs.steam.field_values.vanity_url).toBe('new')
    })

    it('setSourceEnabled also patches the matching syncSources listing entry', async () => {
      mockPut.mockResolvedValueOnce({
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: false,
        migrated: true,
        migrated_at: 'now',
        field_values: {},
        secret_status: {},
      })

      const store = useDataStore()
      // Seed the listing as the page would after loadSyncSources.
      store.syncSources = [listedSource('steam', 'Steam')]

      await store.setSourceEnabled('steam', false)

      expect(store.syncSources[0].enabled).toBe(false)
    })

    it('setSourceSchedule PUTs the interval and re-reads the listing for it', async () => {
      mockPut.mockResolvedValueOnce({
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: {},
        secret_status: {},
        sync_interval: '6h',
        sync_interval_default: 'daily',
      })
      mockGet.mockResolvedValueOnce([
        {
          ...listedSource('steam', 'Steam'),
          sync_interval: '6h',
          next_run_at: '2026-08-17T18:00:00+00:00',
        },
      ])

      const store = useDataStore()
      store.syncSources = [listedSource('steam', 'Steam')]
      await store.setSourceSchedule('steam', '6h')

      expect(mockPut).toHaveBeenCalledWith('/sync/sources/steam/schedule', {
        interval: '6h',
      })
      expect(store.sourceConfigs.steam.sync_interval).toBe('6h')
      // Patching the interval alone would leave next_run_at on the old cadence,
      // which the header quotes as the due time.
      expect(store.syncSources[0].next_run_at).toBe('2026-08-17T18:00:00+00:00')
    })

    it('setSourceSchedule rejects so the caller can report a refusal', async () => {
      mockPut.mockRejectedValueOnce(
        new ApiError(400, 'Bad Request', {
          detail: 'Interval must be one of: off, hourly, 6h, daily, weekly.',
        }),
      )

      const store = useDataStore()

      // Swallowed, the select would snap back to the old cadence with nothing
      // on screen saying the change was refused (WCAG 3.3.1).
      await expect(
        store.setSourceSchedule('steam', 'fortnightly'),
      ).rejects.toBeInstanceOf(ApiError)
    })

    it('loadSourceRuns asks for that source newest-first and caches them', async () => {
      const runs = [
        {
          source_id: 'steam',
          started_at: '2026-08-17T10:00:00+00:00',
          finished_at: '2026-08-17T10:00:30+00:00',
          status: 'failed',
          items_added: 0,
          items_updated: 0,
          items_unchanged: 0,
          total_items: 0,
          errors: ['Steam API returned 401 Unauthorized'],
        },
      ]
      mockGet.mockResolvedValueOnce(runs)

      const store = useDataStore()
      const result = await store.loadSourceRuns('steam')

      expect(mockGet).toHaveBeenCalledWith('/sync/runs', {
        source_id: 'steam',
        limit: 10,
      })
      expect(result).toEqual(runs)
      expect(store.sourceRuns.steam[0].errors).toEqual([
        'Steam API returned 401 Unauthorized',
      ])
    })

    it('loadAvailablePlugins caches the plugins and what failed to load', async () => {
      const plugins = [
        {
          name: 'fake_file',
          display_name: 'Fake File',
          description: 'desc',
          content_types: ['book'],
          requires_api_key: false,
          requires_network: false,
          fields: [],
        },
      ]
      // Regression: the endpoint answered a bare array, so a plugin whose
      // module raised was simply absent and the picker could not say why.
      const importErrors = [
        { module: 'goodreads_rss', reason: "ModuleNotFoundError: No module named 'defusedxml'" },
      ]
      mockGet.mockResolvedValueOnce({ plugins, import_errors: importErrors })

      const store = useDataStore()
      const result = await store.loadAvailablePlugins()

      expect(mockGet).toHaveBeenCalledWith('/plugins')
      expect(result).toEqual(plugins)
      expect(store.availablePlugins).toEqual(plugins)
      expect(store.pluginImportErrors).toEqual(importErrors)
    })

    it('createSource POSTs the payload and refreshes the listing', async () => {
      const created = {
        source_id: 'fresh',
        plugin: 'fake_file',
        plugin_display_name: 'Fake File',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: {},
        secret_status: {},
      }
      mockPost.mockResolvedValueOnce(created)
      // Subsequent loadSyncSources call (config/reload, then the listing).
      mockPost.mockResolvedValueOnce({})
      mockGet.mockResolvedValueOnce([
        { id: 'fresh', display_name: 'Fresh', plugin_display_name: 'Fake File', enabled: true },
      ])

      const store = useDataStore()
      const payload = {
        id: 'fresh',
        plugin: 'fake_file',
        values: {},
        enabled: true,
      }
      const result = await store.createSource(payload)

      expect(mockPost).toHaveBeenNthCalledWith(1, '/sync/sources', payload)
      expect(result).toEqual(created)
      expect(store.sourceConfigs.fresh).toEqual(created)
      // Listing was refreshed from the server, not synthesised locally.
      expect(store.syncSources.map((s) => s.id)).toEqual(['fresh'])
    })

    it('deleteSource DELETEs and prunes the listing + caches', async () => {
      mockDelete.mockResolvedValueOnce(null)

      const store = useDataStore()
      store.syncSources = [
        listedSource('goner', 'Goner'),
        listedSource('survivor', 'Survivor'),
      ]
      store.sourceConfigs = {
        goner: {
          source_id: 'goner',
          plugin: 'fake_file',
          plugin_display_name: 'Fake File',
          enabled: true,
          migrated: true,
          migrated_at: 'now',
          field_values: {},
          secret_status: {},
          sync_interval: 'off',
          sync_interval_default: 'off',
        },
      }
      store.sourceRuns = {
        goner: [
          {
            source_id: 'goner',
            started_at: '2026-08-17T10:00:00+00:00',
            finished_at: '2026-08-17T10:01:00+00:00',
            status: 'failed',
            items_added: 0,
            items_updated: 0,
            items_unchanged: 0,
            total_items: 0,
            errors: ['401 Unauthorized'],
          },
        ],
      }
      store.oauthStatus = {
        goner: { enabled: true, connected: true, authUrl: null },
      }
      store.oauthMessages = { goner: 'Disconnected. You can reconnect below.' }

      await store.deleteSource('goner')

      expect(mockDelete).toHaveBeenCalledWith('/sync/sources/goner')
      expect(store.syncSources.map((s) => s.id)).toEqual(['survivor'])
      expect(store.sourceConfigs.goner).toBeUndefined()
      // A source id is reusable, so leaving these behind hands the next source
      // of that name a connection state and an announcement it never earned.
      expect(store.oauthStatus.goner).toBeUndefined()
      expect(store.oauthMessages.goner).toBeUndefined()
      expect(store.sourceRuns.goner).toBeUndefined()
    })

    it('drops a status read still in flight when the source is deleted', async () => {
      const answer = pendingStatusReads()

      const store = useDataStore()
      // Remove is disabled on removing/syncing, not on a pending recheck, so a
      // read outliving its source is reachable from the panel.
      const inFlight = store.loadOAuthStatus('goner', 'trakt')
      mockDelete.mockResolvedValueOnce(null)
      await store.deleteSource('goner')

      answer[0]({ enabled: true, connected: true, auth_url: null })
      await inFlight

      // Landing after the prune, this re-seeded the id, and a source recreated
      // under it rendered "connected" until the fresh read corrected it.
      expect(store.oauthStatus.goner).toBeUndefined()
    })
  })
})
