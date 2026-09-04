import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'
import { ApiError } from '@/composables/useApi'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

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

  function listedSource(id: string, displayName: string) {
    return {
      id,
      display_name: displayName,
      plugin_display_name: 'Fake File',
      enabled: true,
      plugin_not_loaded: null,
      sync_interval: 'off',
      last_run_at: null,
      last_run_status: null,
      next_run_at: null,
    }
  }

  function ranSources(omittedBySource: Record<string, number>) {
    return Object.entries(omittedBySource).map(([source, omitted_errors]) => ({
      source,
      items_processed: 0,
      total_items: null,
      current_item: null,
      progress_percent: null,
      omitted_errors,
    }))
  }

  it('triggerSync marks the source label as syncing', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for Steam', sources: ['steam'] })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('running')
    expect(store.syncMessage).toBe('Sync started for Steam')
    expect(store.isSourceIdSyncing('steam')).toBe(true)
    expect(store.isSourceIdSyncing('goodreads')).toBe(false)
  })

  it('triggerSync clears the optimistic trigger and starts no polling on API error', async () => {
    mockPost.mockRejectedValue(new ApiError(503, 'Service Unavailable'))

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.syncMessage).toContain('503 Service Unavailable')
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGet.mock.calls.length).toBe(callsBefore)
  })

  it('triggerSync reports the refusal detail rather than the status code', async () => {
    mockPost.mockRejectedValue(
      new ApiError(409, 'Conflict', { detail: 'A sync is already in progress' }),
    )

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncMessage).toBe('Error: A sync is already in progress')
  })

  it('triggerSync keeps the optimistic flag and starts polling on a successful start', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for Steam', sources: ['steam'] })
    mockGet.mockResolvedValue({ status: 'idle', jobs: [] })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.isSourceIdSyncing('steam')).toBe(true)
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGet.mock.calls.length).toBe(callsBefore + 1)
    expect(mockGet).toHaveBeenLastCalledWith('/sync/status')

    store.cleanup()
  })

  it('triggerSync releases the button when the response started no job', async () => {
    mockPost.mockResolvedValue({ message: 'No sources enabled or configured for sync' })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('all')

    expect(store.isSourceIdSyncing('all')).toBe(false)
    expect(store.anySyncRunning).toBe(false)
    expect(store.syncMessage).toBe('No sources enabled or configured for sync')
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGet.mock.calls.length).toBe(callsBefore)
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
    expect(store.syncMessage).toBe(
      'Completed (Steam): 42 of 42 items saved (2 added, 0 updated, 40 unchanged)',
    )
    expect(store.isSourceIdSyncing('steam')).toBe(false)
  })

  it('checkSyncStatus reports the run that just finished, not every retained job', async () => {
    mockGet.mockResolvedValue({
      status: 'idle',
      jobs: [
        {
          source: 'Steam',
          status: 'completed',
          started_at: '2026-08-23T10:00:00',
          items_processed: 8,
          total_items: 9,
          items_added: 5,
          items_updated: 2,
          items_unchanged: 1,
          errors: [],
          sources: [],
        },
        {
          source: 'Roms',
          status: 'completed',
          started_at: '2026-08-23T10:05:00',
          items_processed: 3,
          total_items: 3,
          items_added: 3,
          items_updated: 0,
          items_unchanged: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('Roms')
    expect(store.syncMessage).toContain('3 of 3')
    expect(store.syncMessage).not.toContain('11 of 12')
  })

  it('checkSyncStatus starts enrichment polling when a completed sync auto-triggered enrichment', async () => {
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
      .mockResolvedValueOnce(runningStatus)
      .mockResolvedValueOnce(stats)

    const store = useDataStore()
    await store.checkSyncStatus()
    await vi.advanceTimersByTimeAsync(0)

    expect(store.syncStatus).toBe('completed')
    expect(store.enrichmentJob).toEqual(runningStatus)
    expect(store.enrichmentStats).toEqual(stats)

    const tickStatus = { ...runningStatus, items_processed: 30, items_enriched: 30, progress_percent: 60 }
    const tickStats = { ...stats, enriched: 30, pending: 20 }
    mockGet.mockResolvedValueOnce(tickStatus).mockResolvedValueOnce(tickStats)
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockGet).toHaveBeenCalledWith('/enrichment/status')
    expect(store.enrichmentJob).toEqual(tickStatus)
    expect(store.enrichmentStats).toEqual(tickStats)

    store.cleanup()
  })

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
            sources: ranSources({ Sonarr: 4800, Steam: 0 }),
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

      expect(store.jobForSourceId('steam')?.source).toBe('All Sources')
      expect(store.jobForSourceId('sonarr')?.source).toBe('All Sources')
      expect(store.syncMessage).toContain('Sonarr: TLS verification failed')
      expect(store.syncMessage).toContain('4801')
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
            sources: ranSources({ Sonarr: 0, Steam: 4800 }),
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

  it('counts the errors a running run had, not the ones its payload carried', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'All Sources',
          status: 'running',
          items_processed: 200,
          total_items: 6000,
          errors: Array.from({ length: 200 }, (_, index) => ({
            source: 'Steam',
            message: `Steam ${index} failed`,
          })),
          sources: [
            {
              source: 'Steam',
              items_processed: 0,
              total_items: 5000,
              current_item: null,
              progress_percent: 0,
              omitted_errors: 4800,
            },
          ],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('5000')
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

  describe('an enrichment action the server refuses', () => {
    it('rejects out of startEnrichment rather than swallowing the reason', async () => {
      mockPost.mockRejectedValue(new ApiError(400, 'Enrichment is disabled.'))
      const store = useDataStore()

      await expect(store.startEnrichment()).rejects.toThrow('Enrichment is disabled.')
    })

    it('rejects out of stopEnrichment rather than swallowing the reason', async () => {
      mockPost.mockRejectedValue(new ApiError(409, 'No job is running.'))
      const store = useDataStore()

      await expect(store.stopEnrichment()).rejects.toThrow('No job is running.')
    })

    it('rejects out of resetEnrichment rather than swallowing the reason', async () => {
      mockPost.mockRejectedValue(new ApiError(500, 'Reset failed.'))
      const store = useDataStore()

      await expect(store.resetEnrichment()).rejects.toThrow('Reset failed.')
    })

    it('sends the provider filter the CLI offers on reset', async () => {
      mockPost.mockResolvedValue({ message: 'Reset 3 item(s)', count: 3 })
      mockGet.mockResolvedValue({
        enabled: true,
        total: 3,
        enriched: 0,
        pending: 3,
        not_found: 0,
        failed: 0,
        by_provider: {},
        by_quality: {},
      })
      const store = useDataStore()

      await store.resetEnrichment('movie', 'rawg')

      expect(mockPost).toHaveBeenCalledWith('/enrichment/reset', {
        content_type: 'movie',
        provider: 'rawg',
        user_id: 1,
      })
    })

    it('keeps the counts a failed stats read could not replace, and says it failed', async () => {
      const store = useDataStore()
      mockGet.mockResolvedValueOnce({
        enabled: true,
        total: 10,
        enriched: 4,
        pending: 6,
        not_found: 0,
        failed: 0,
        by_provider: {},
        by_quality: {},
      })
      await store.loadEnrichmentStats()

      mockGet.mockRejectedValueOnce(new ApiError(503, 'backend is down'))
      await store.loadEnrichmentStats()

      expect(store.enrichmentStatsError).toContain('backend is down')
      expect(store.enrichmentStats?.total).toBe(10)
    })

    it('keeps the last known job when a status poll drops', async () => {
      const running = {
        running: true,
        completed: false,
        cancelled: false,
        items_processed: 1,
        items_enriched: 1,
        items_failed: 0,
        items_not_found: 0,
        total_items: 2,
        current_item: null,
        content_type: null,
        errors: [],
        elapsed_seconds: 1,
        progress_percent: 50,
      }
      const store = useDataStore()
      mockGet.mockResolvedValueOnce(running).mockResolvedValueOnce({
        enabled: true,
        total: 2,
        enriched: 1,
        pending: 1,
        not_found: 0,
        failed: 0,
        by_provider: {},
        by_quality: {},
      })
      await store.checkEnrichmentStatus()

      mockGet.mockRejectedValueOnce(new Error('network'))
      await store.checkEnrichmentStatus()
      store.cleanup()

      expect(store.enrichmentJob).toEqual(running)
    })
  })

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
      expect(store.oauthStatusFor('other').connected).toBe(false)
    })

    it('keeps the newer status when an overtaken read answers last', async () => {
      const answer = pendingStatusReads()

      const store = useDataStore()
      const overtaken = store.loadOAuthStatus('trakt_work', 'trakt')
      const latest = store.loadOAuthStatus('trakt_work', 'trakt')

      answer[1]({ enabled: true, connected: false, auth_url: null })
      await latest
      answer[0]({ enabled: false, connected: false, auth_url: null })
      await overtaken

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

      expect(store.oauthMessages['gog_work']).toBe(
        'Error: GOG is not enabled for that source.',
      )
    })

    it('submitGogCode keeps the confirmation and rejects when the re-read fails', async () => {
      mockPost.mockResolvedValueOnce({ message: 'GOG account connected!' })
      mockGet.mockRejectedValueOnce(new ApiError(503, 'Service Unavailable'))

      const store = useDataStore()

      await expect(
        store.submitGogCode('gog_work', 'auth-code'),
      ).rejects.toBeInstanceOf(ApiError)
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

      await store.disconnectTrakt('trakt_work')

      expect(store.oauthMessages['trakt_work']).toBe(
        'Error: No active Trakt connection found',
      )
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
      expect(store.syncSources[0].next_run_at).toBe('2026-08-17T18:00:00+00:00')
    })

    it('setSourceSchedule rejects so the caller can report a refusal', async () => {
      mockPut.mockRejectedValueOnce(
        new ApiError(400, 'Bad Request', {
          detail: 'Interval must be one of: off, hourly, 6h, daily, weekly.',
        }),
      )

      const store = useDataStore()

      await expect(
        store.setSourceSchedule('steam', 'fortnightly'),
      ).rejects.toBeInstanceOf(ApiError)
    })

    it('loadSourceRuns asks for that source only and caches what came back', async () => {
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
      await store.loadSourceRuns('steam')

      expect(mockGet).toHaveBeenCalledWith('/sync/runs', {
        source_id: 'steam',
        limit: 20,
      })
      expect(store.sourceRuns.steam).toEqual(runs)
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
        },
      }
      store.oauthStatus = {
        goner: { enabled: true, connected: true, authUrl: null },
      }
      store.oauthMessages = { goner: 'Disconnected. You can reconnect below.' }
      store.sourceRuns = { goner: [] }

      await store.deleteSource('goner')

      expect(mockDelete).toHaveBeenCalledWith('/sync/sources/goner')
      expect(store.syncSources.map((s) => s.id)).toEqual(['survivor'])
      expect(store.sourceConfigs.goner).toBeUndefined()
      expect(store.oauthStatus.goner).toBeUndefined()
      expect(store.oauthMessages.goner).toBeUndefined()
      expect(store.sourceRuns.goner).toBeUndefined()
    })

    it('drops a status read still in flight when the source is deleted', async () => {
      const answer = pendingStatusReads()

      const store = useDataStore()
      const inFlight = store.loadOAuthStatus('goner', 'trakt')
      mockDelete.mockResolvedValueOnce(null)
      await store.deleteSource('goner')

      answer[0]({ enabled: true, connected: true, auth_url: null })
      await inFlight

      expect(store.oauthStatus.goner).toBeUndefined()
    })
  })
})
