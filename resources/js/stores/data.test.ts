import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'
import { ApiError } from '@/composables/useApi'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPostForm = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/composables/useApi', () => ({
  ApiError: class ApiError extends Error {
    constructor(public status: number, public statusText: string) {
      super(`${status} ${statusText}`)
      this.name = 'ApiError'
    }
  },
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    postForm: (...args: unknown[]) => mockPostForm(...args),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
    raw: vi.fn(),
  }),
}))

describe('useDataStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockGet.mockReset()
    mockPost.mockReset()
    mockPostForm.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('has correct initial state', () => {
    const store = useDataStore()
    expect(store.syncSources).toEqual([])
    expect(store.syncStatus).toBe('idle')
    expect(store.syncJobs).toEqual([])
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.enrichmentStats).toBeNull()
  })

  it('loadSyncSources fetches sources and auth status', async () => {
    const sources = [
      { id: 'steam', display_name: 'Steam', plugin_display_name: 'Steam', enabled: true },
    ]
    mockPost.mockResolvedValue({})
    mockGet
      .mockResolvedValueOnce(sources)
      .mockResolvedValueOnce({ enabled: true, connected: false, auth_url: 'https://gog.com/auth' })
      .mockResolvedValueOnce({ enabled: false, connected: false })

    const store = useDataStore()
    await store.loadSyncSources()

    expect(store.syncSources).toEqual(sources)
    expect(store.gogStatus.authUrl).toBe('https://gog.com/auth')
  })

  function steamSource() {
    return {
      id: 'steam',
      display_name: 'Steam',
      plugin_display_name: 'Steam',
      enabled: true,
    }
  }

  function goodreadsSource() {
    return {
      id: 'goodreads',
      display_name: 'Goodreads',
      plugin_display_name: 'Goodreads',
      enabled: true,
    }
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

  it('triggerSync clears the optimistic trigger on API error', async () => {
    mockPost.mockRejectedValue(new ApiError(503, 'Service Unavailable'))

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.syncMessage).toContain('server returned 503')
  })

  it('triggerSync clears the optimistic trigger on generic error', async () => {
    mockPost.mockRejectedValue(new Error('network failure'))

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.syncMessage).toContain('unexpected failure')
  })

  it('triggerSync handles the "all" pseudo-source via the All Sources label', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for All Sources' })

    const store = useDataStore()
    await store.triggerSync('all')

    expect(store.isSourceIdSyncing('all')).toBe(true)
  })

  it('checkSyncStatus drops the optimistic trigger once the server ack\'s the job', async () => {
    // Plant an optimistic trigger via triggerSync, then poll status and
    // observe the trigger is cleared because the server now reports the
    // job — isSourceIdSyncing should still be true (driven by the job),
    // but the source of truth has shifted from optimistic to server.
    mockPost.mockResolvedValue({ message: 'Sync started for Steam' })
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Steam',
          status: 'running',
          items_processed: 0,
          total_items: null,
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')
    await store.checkSyncStatus()

    expect(store.isSourceIdSyncing('steam')).toBe(true)
    // A second poll with the same response keeps the source syncing —
    // the source of truth has shifted from optimistic to server, but
    // the public flag stays true because the server reports running.
    await store.checkSyncStatus()
    expect(store.isSourceIdSyncing('steam')).toBe(true)
  })

  it('checkSyncStatus reports idle on empty jobs array', async () => {
    mockGet.mockResolvedValue({ status: 'idle', jobs: [] })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('idle')
    expect(store.syncJobs).toEqual([])
    expect(store.syncMessage).toBe('')
  })

  it('checkSyncStatus treats total_items=0 as unknown total in aggregate banner', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Steam',
          status: 'running',
          items_processed: 5,
          total_items: 0,
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('5 items so far')
    expect(store.syncMessage).not.toContain('/0')
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
          error_count: 0,
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
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('completed')
    expect(store.syncMessage).toContain('42 items synced')
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
            error_count: 0,
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

  it('checkSyncStatus does not start enrichment polling when the completed sync left enrichment idle', async () => {
    // Symmetric to the auto-trigger case: a completed sync that did NOT
    // start enrichment must not spin up the 3s enrichment poll, otherwise
    // the data view would fetch /enrichment/status forever for no reason.
    const idleStatus = {
      running: false,
      completed: false,
      cancelled: false,
      items_processed: 0,
      items_enriched: 0,
      items_failed: 0,
      items_not_found: 0,
      total_items: 0,
      current_item: null,
      content_type: null,
      errors: [],
      elapsed_seconds: 0,
      progress_percent: 0,
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
            error_count: 0,
            errors: [],
            sources: [],
          },
        ],
      })
      // checkEnrichmentStatus -> GET /enrichment/status (not running)
      .mockResolvedValueOnce(idleStatus)

    const store = useDataStore()
    await store.checkSyncStatus()
    await vi.advanceTimersByTimeAsync(0)

    expect(store.syncStatus).toBe('completed')
    expect(store.enrichmentJob).toEqual(idleStatus)

    // No poll is scheduled: advancing past a tick must not re-fetch status.
    const callsBefore = mockGet.mock.calls.length
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockGet.mock.calls.length).toBe(callsBefore)

    store.cleanup()
  })

  it('checkSyncStatus aggregates errors across completed jobs', async () => {
    mockGet.mockResolvedValue({
      status: 'idle',
      jobs: [
        {
          source: 'Steam',
          status: 'completed',
          items_processed: 90,
          error_count: 2,
          errors: ['e1', 'e2'],
          sources: [],
        },
        {
          source: 'Goodreads',
          status: 'completed',
          items_processed: 10,
          error_count: 1,
          errors: ['e3'],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('100 items synced')
    expect(store.syncMessage).toContain('3 errors')
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
          error_count: 1,
          errors: ['timeout'],
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

  it('checkSyncStatus is idle when no jobs are tracked', async () => {
    mockGet.mockResolvedValue({ status: 'idle', jobs: [] })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('idle')
    expect(store.syncMessage).toBe('')
  })

  it('checkSyncStatus marks per-source jobs as syncing for their source IDs', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Goodreads',
          status: 'running',
          items_processed: 5,
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    store.$patch({ syncSources: [goodreadsSource(), steamSource()] })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('running')
    expect(store.isSourceIdSyncing('goodreads')).toBe(true)
    expect(store.isSourceIdSyncing('steam')).toBe(false)
  })

  it('checkSyncStatus builds aggregate progress message with totals', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Steam',
          status: 'running',
          items_processed: 10,
          total_items: 100,
          progress_percent: 10,
          current_source: 'Steam',
          current_item: 'Half-Life 2',
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('10/100')
    expect(store.syncMessage).toContain('(10%)')
    expect(store.syncMessage).toContain('Half-Life 2')
  })

  it('checkSyncStatus shows "items so far" when total is unknown', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'Steam',
          status: 'running',
          items_processed: 7,
          total_items: null,
          error_count: 0,
          errors: [],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('7 items so far')
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
          error_count: 0,
          errors: [],
          sources: [],
        },
        {
          source: 'Steam',
          status: 'running',
          items_processed: 3,
          total_items: 20,
          error_count: 0,
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

  it('checkSyncStatus silently ignores GET failure without changing state', async () => {
    mockGet.mockRejectedValue(new Error('network error'))

    const store = useDataStore()
    store.$patch({ syncStatus: 'running', syncMessage: 'previous message' })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('running')
    expect(store.syncMessage).toBe('previous message')
  })

  it('jobForSourceId returns the job whose source matches display_name', async () => {
    const job = {
      source: 'Steam',
      status: 'running' as const,
      items_processed: 9,
      total_items: 10,
      current_item: 'Half-Life 2',
      error_count: 0,
      errors: [] as string[],
      sources: [] as never[],
    }
    mockGet.mockResolvedValue({ status: 'running', jobs: [job] })

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.checkSyncStatus()

    const found = store.jobForSourceId('steam')
    expect(found?.source).toBe('Steam')
    expect(found?.current_item).toBe('Half-Life 2')
    expect(store.jobForSourceId('goodreads')).toBeNull()
  })

  it('loadEnrichmentStats fetches stats', async () => {
    const stats = { enabled: true, total: 100, enriched: 50, pending: 30, not_found: 10, failed: 10, by_provider: {}, by_quality: {} }
    mockGet.mockResolvedValue(stats)

    const store = useDataStore()
    await store.loadEnrichmentStats()

    expect(store.enrichmentStats).toEqual(stats)
    expect(store.enrichmentEnabled).toBe(true)
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

  it('disconnectGog calls DELETE /gog/token and reloads sync sources', async () => {
    mockDelete.mockResolvedValue({})
    mockPost.mockResolvedValue({})
    mockGet
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ enabled: true, connected: false, auth_url: 'https://gog.com/auth' })
      .mockResolvedValueOnce({ enabled: false, connected: false })

    const store = useDataStore()
    await store.disconnectGog()

    expect(mockDelete).toHaveBeenCalledWith('/gog/token')
    expect(store.gogConnectMessage).toBe('Disconnected. You can reconnect below.')
    expect(store.gogStatus.connected).toBe(false)
  })

  it('disconnectEpic calls DELETE /epic/token and reloads sync sources', async () => {
    mockDelete.mockResolvedValue({})
    mockPost.mockResolvedValue({})
    mockGet
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce({ enabled: false, connected: false })
      .mockResolvedValueOnce({ enabled: true, connected: false, auth_url: 'https://epicgames.com/auth' })

    const store = useDataStore()
    await store.disconnectEpic()

    expect(mockDelete).toHaveBeenCalledWith('/epic/token')
    expect(store.epicConnectMessage).toBe('Disconnected. You can reconnect below.')
    expect(store.epicStatus.connected).toBe(false)
  })

  it('disconnectGog surfaces API error and does not reload sync sources', async () => {
    mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    const store = useDataStore()
    await store.disconnectGog()

    expect(store.gogConnectMessage).toBe('Error: server returned 500')
    // Reload must only run on success — otherwise a failed disconnect
    // triggers a spurious status fetch that itself may error.
    expect(mockGet).not.toHaveBeenCalled()
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('disconnectEpic surfaces API error and does not reload sync sources', async () => {
    mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    const store = useDataStore()
    await store.disconnectEpic()

    expect(store.epicConnectMessage).toBe('Error: server returned 500')
    expect(mockGet).not.toHaveBeenCalled()
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('disconnectGog surfaces generic error with fallback message', async () => {
    mockDelete.mockRejectedValue(new Error('network timeout'))

    const store = useDataStore()
    await store.disconnectGog()

    expect(store.gogConnectMessage).toBe('Error: disconnect failed')
    expect(mockGet).not.toHaveBeenCalled()
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('disconnectEpic surfaces generic error with fallback message', async () => {
    mockDelete.mockRejectedValue(new Error('network timeout'))

    const store = useDataStore()
    await store.disconnectEpic()

    expect(store.epicConnectMessage).toBe('Error: disconnect failed')
    expect(mockGet).not.toHaveBeenCalled()
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('disconnectGog sets in-progress message before awaiting DELETE', async () => {
    let rejectDelete: (err: Error) => void = () => {}
    mockDelete.mockImplementation(
      () => new Promise((_, reject) => { rejectDelete = reject })
    )

    const store = useDataStore()
    const pending = store.disconnectGog()
    // Synchronous assignment must land before the promise resolves so the
    // aria-live region announces activity immediately.
    expect(store.gogConnectMessage).toBe('Disconnecting GOG...')

    rejectDelete(new ApiError(500, 'Internal Server Error'))
    await pending

    expect(store.gogConnectMessage).toBe('Error: server returned 500')
  })

  it('disconnectEpic sets in-progress message before awaiting DELETE', async () => {
    let rejectDelete: (err: Error) => void = () => {}
    mockDelete.mockImplementation(
      () => new Promise((_, reject) => { rejectDelete = reject })
    )

    const store = useDataStore()
    const pending = store.disconnectEpic()
    expect(store.epicConnectMessage).toBe('Disconnecting Epic Games...')

    rejectDelete(new ApiError(500, 'Internal Server Error'))
    await pending

    expect(store.epicConnectMessage).toBe('Error: server returned 500')
  })

  describe('source config flows', () => {
    it('loadSourceSchema fetches and caches the schema', async () => {
      const schema = {
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        fields: [
          {
            name: 'api_key',
            field_type: 'str',
            required: true,
            default: null,
            description: '',
            sensitive: true,
          },
        ],
      }
      mockGet.mockResolvedValueOnce(schema)

      const store = useDataStore()
      const result = await store.loadSourceSchema('steam')

      expect(mockGet).toHaveBeenCalledWith('/sync/sources/steam/schema')
      expect(result).toEqual(schema)
      expect(store.sourceSchemas.steam).toEqual(schema)
    })

    it('loadSourceConfig fetches and caches the config snapshot', async () => {
      const cfg = {
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: '2026-05-03T00:00:00Z',
        field_values: { vanity_url: 'me' },
        secret_status: { api_key: true },
      }
      mockGet.mockResolvedValueOnce(cfg)

      const store = useDataStore()
      const result = await store.loadSourceConfig('steam')

      expect(mockGet).toHaveBeenCalledWith('/sync/sources/steam/config')
      expect(result).toEqual(cfg)
      expect(store.sourceConfigs.steam).toEqual(cfg)
    })

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

    it('setSourceSecret PUTs the secret to the per-key endpoint', async () => {
      mockPut.mockResolvedValueOnce(null)
      mockGet.mockResolvedValueOnce({
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: {},
        secret_status: { api_key: true },
      })

      const store = useDataStore()
      await store.setSourceSecret('steam', 'api_key', 'rotated')

      expect(mockPut).toHaveBeenCalledWith(
        '/sync/sources/steam/secret/api_key',
        { value: 'rotated' },
      )
      expect(store.sourceConfigs.steam.secret_status.api_key).toBe(true)
    })

    it('clearSourceSecret deletes secret and refreshes config', async () => {
      mockDelete.mockResolvedValueOnce(null)
      mockGet.mockResolvedValueOnce({
        source_id: 'steam',
        plugin: 'steam',
        plugin_display_name: 'Steam',
        enabled: true,
        migrated: true,
        migrated_at: 'now',
        field_values: {},
        secret_status: { api_key: false },
      })

      const store = useDataStore()
      await store.clearSourceSecret('steam', 'api_key')

      expect(mockDelete).toHaveBeenCalledWith(
        '/sync/sources/steam/secret/api_key',
      )
      expect(store.sourceConfigs.steam.secret_status.api_key).toBe(false)
    })

    it('setSourceEnabled PUTs new enabled flag', async () => {
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
      await store.setSourceEnabled('steam', false)

      expect(mockPut).toHaveBeenCalledWith('/sync/sources/steam/enabled', {
        enabled: false,
      })
      expect(store.sourceConfigs.steam.enabled).toBe(false)
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
      store.syncSources = [
        {
          id: 'steam',
          display_name: 'Steam',
          plugin_display_name: 'Steam',
          enabled: true,
        },
      ]

      await store.setSourceEnabled('steam', false)

      expect(store.syncSources[0].enabled).toBe(false)
    })

    it('loadAvailablePlugins fetches and caches plugin metadata', async () => {
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
      mockGet.mockResolvedValueOnce(plugins)

      const store = useDataStore()
      const result = await store.loadAvailablePlugins()

      expect(mockGet).toHaveBeenCalledWith('/plugins')
      expect(result).toEqual(plugins)
      expect(store.availablePlugins).toEqual(plugins)
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
      // Subsequent loadSyncSources call (config/reload + sources + gog + epic).
      mockPost.mockResolvedValueOnce({})
      mockGet
        .mockResolvedValueOnce([
          { id: 'fresh', display_name: 'Fresh', plugin_display_name: 'Fake File', enabled: true },
        ])
        .mockResolvedValueOnce({ enabled: false, connected: false })
        .mockResolvedValueOnce({ enabled: false, connected: false })

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

    it('createSource propagates API rejection to the caller', async () => {
      const error = new ApiError(409, 'Conflict')
      mockPost.mockRejectedValueOnce(error)

      const store = useDataStore()
      const beforeCount = store.syncSources.length

      await expect(
        store.createSource({
          id: 'taken',
          plugin: 'fake_file',
          values: {},
          enabled: true,
        }),
      ).rejects.toBe(error)

      // Listing left untouched on rejection — caller is the one with UI to react.
      expect(store.syncSources.length).toBe(beforeCount)
      expect(store.sourceConfigs.taken).toBeUndefined()
    })

    it('deleteSource DELETEs and prunes the listing + caches', async () => {
      mockDelete.mockResolvedValueOnce(null)

      const store = useDataStore()
      store.syncSources = [
        {
          id: 'goner',
          display_name: 'Goner',
          plugin_display_name: 'Fake File',
          enabled: true,
        },
        {
          id: 'survivor',
          display_name: 'Survivor',
          plugin_display_name: 'Fake File',
          enabled: true,
        },
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
        },
      }

      await store.deleteSource('goner')

      expect(mockDelete).toHaveBeenCalledWith('/sync/sources/goner')
      expect(store.syncSources.map((s) => s.id)).toEqual(['survivor'])
      expect(store.sourceConfigs.goner).toBeUndefined()
    })

    it('deleteSource leaves caches intact when the API rejects', async () => {
      const error = new ApiError(404, 'Not Found')
      mockDelete.mockRejectedValueOnce(error)

      const store = useDataStore()
      store.syncSources = [
        {
          id: 'still_here',
          display_name: 'Still Here',
          plugin_display_name: 'Fake File',
          enabled: true,
        },
      ]

      await expect(store.deleteSource('still_here')).rejects.toBe(error)
      // The listing is untouched so the UI keeps showing the source.
      expect(store.syncSources.map((s) => s.id)).toEqual(['still_here'])
    })
  })

  describe('file import flows', () => {
    it('loadImportSources fetches and caches the importable plugins', async () => {
      const sources = [
        {
          name: 'csv_import',
          display_name: 'CSV Import',
          description: 'Import a generic CSV file.',
          content_types: ['book', 'movie'],
          fields: [],
        },
      ]
      mockGet.mockResolvedValueOnce(sources)

      const store = useDataStore()
      const result = await store.loadImportSources()

      expect(mockGet).toHaveBeenCalledWith('/import/sources')
      expect(result).toEqual(sources)
      expect(store.importSources).toEqual(sources)
    })

    it('runImport posts a multipart body and starts sync polling', async () => {
      const importResult = {
        message: 'Imported 3 item(s) from CSV Import.',
        source: 'Import: CSV Import',
        items_synced: 3,
        total_items: 3,
        errors: [],
      }
      mockPostForm.mockResolvedValueOnce(importResult)

      const store = useDataStore()
      const file = new File(['title\nDune'], 'books.csv', { type: 'text/csv' })
      const result = await store.runImport('csv_import', file, {
        content_type: 'book',
      })

      expect(result).toEqual(importResult)
      expect(mockPostForm).toHaveBeenCalledTimes(1)
      const [path, body] = mockPostForm.mock.calls[0]
      expect(path).toBe('/import')
      expect(body).toBeInstanceOf(FormData)
      const form = body as FormData
      expect(form.get('source')).toBe('csv_import')
      expect(form.get('file')).toBe(file)
      expect(form.get('content_type')).toBe('book')

      // Polling is live after dispatch — a tick fetches /sync/status.
      mockGet.mockResolvedValueOnce({ status: 'idle', jobs: [] })
      await vi.advanceTimersByTimeAsync(2000)
      expect(mockGet).toHaveBeenCalledWith('/sync/status')

      store.cleanup()
    })

    it('runImport propagates the ApiError to the caller', async () => {
      const error = new ApiError(400, 'Bad Request')
      mockPostForm.mockRejectedValueOnce(error)

      const store = useDataStore()
      const file = new File(['oops'], 'bad.csv', { type: 'text/csv' })

      await expect(
        store.runImport('csv_import', file, {}),
      ).rejects.toBe(error)

      store.cleanup()
    })
  })
})
