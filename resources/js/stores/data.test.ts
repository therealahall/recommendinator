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

  it('has correct initial state', () => {
    const store = useDataStore()
    expect(store.syncSources).toEqual([])
    expect(store.syncStatus).toBe('idle')
    expect(store.syncJobs).toEqual([])
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.enrichmentStats).toBeNull()
  })

  it('loadSyncSources fetches the source list alone', async () => {
    const sources = [
      { id: 'steam', display_name: 'Steam', plugin_display_name: 'Steam', enabled: true },
    ]
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValueOnce(sources)

    const store = useDataStore()
    await store.loadSyncSources()

    expect(store.syncSources).toEqual(sources)
    // OAuth status is per source, so it is read when a source is opened.
    expect(mockGet).toHaveBeenCalledTimes(1)
  })

  // Regression: the catch emptied syncSources, so a failed read reached the
  // page as "No sync sources configured" — telling the user to go and edit
  // config.yaml over a request that never landed.
  it('loadSyncSources records the failure and keeps the loaded list', async () => {
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValueOnce([
      { id: 'steam', display_name: 'Steam', plugin_display_name: 'Steam', enabled: true },
    ])
    const store = useDataStore()
    await store.loadSyncSources()

    mockGet.mockRejectedValueOnce(new Error('Network error'))
    await store.loadSyncSources()

    expect(store.syncSourcesError).toBe('Network error')
    expect(store.syncSources.map((s) => s.id)).toEqual(['steam'])
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

  it('triggerSync clears the optimistic trigger on generic error', async () => {
    mockPost.mockRejectedValue(new Error('network failure'))

    const store = useDataStore()
    store.$patch({ syncSources: [steamSource()] })
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.isSourceIdSyncing('steam')).toBe(false)
    expect(store.syncMessage).toContain('unexpected failure')
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

  it('checkSyncStatus counts errors while the run is still going', async () => {
    // Errors reach the job as each source finishes. Before that they only
    // arrived once the whole run was over, so a multi-source run showed
    // nothing of a source that had already failed.
    mockGet.mockResolvedValue({
      status: 'running',
      jobs: [
        {
          source: 'All Sources',
          status: 'running',
          items_processed: 5,
          total_items: 20,
          current_item: 'Portal 2',
          errors: [
            { source: 'Sonarr', message: 'Set verify_ssl to false' },
            { source: 'Steam', message: 'Rate limit exceeded' },
          ],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('2 error(s) so far')
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
      'Completed: 42 of 42 items saved (2 added, 0 updated, 40 unchanged)',
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

  it('checkSyncStatus reports the first error in full, not a count', async () => {
    mockGet.mockResolvedValue({
      status: 'idle',
      jobs: [
        {
          source: 'All Sources',
          status: 'completed',
          items_processed: 90,
          total_items: 90,
          items_added: 90,
          items_updated: 0,
          items_unchanged: 0,
          errors: [
            { source: 'Sonarr', message: 'Set verify_ssl to false' },
            { source: 'Steam', message: 'Rate limit exceeded' },
          ],
          sources: ranSources('Sonarr', 'Steam'),
        },
        {
          source: 'Goodreads',
          status: 'completed',
          items_processed: 10,
          total_items: 10,
          items_added: 10,
          items_updated: 0,
          items_unchanged: 0,
          errors: [{ source: 'Goodreads', message: 'Row 4 has no title' }],
          sources: [],
        },
      ],
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('100 of 100 items saved')
    expect(store.syncMessage).toContain('Sonarr: Set verify_ssl to false')
    expect(store.syncMessage).toContain('(+2 more)')
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
      errors: [],
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

    it('lets two sources recheck at once without cancelling each other', async () => {
      const answer = pendingStatusReads()

      const store = useDataStore()
      const work = store.loadOAuthStatus('trakt_work', 'trakt')
      const home = store.loadOAuthStatus('trakt_home', 'trakt')

      answer[1]({ enabled: true, connected: true, auth_url: null })
      await home
      answer[0]({ enabled: true, connected: true, auth_url: null })
      await work

      // Both sources run the one OAuth plugin. A counter keyed on the plugin,
      // or one counter for the whole store, reads the trakt_work answer as
      // overtaken and drops a status nobody asks for again.
      expect(store.oauthStatusFor('trakt_work').connected).toBe(true)
      expect(store.oauthStatusFor('trakt_home').connected).toBe(true)
    })

    it('loadOAuthStatus asks nothing for a plugin with no OAuth flow', async () => {
      const store = useDataStore()
      await store.loadOAuthStatus('my_books', 'goodreads_csv')

      expect(mockGet).not.toHaveBeenCalled()
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

    it('submitEpicCode posts the code for that source and re-reads its status', async () => {
      mockPost.mockResolvedValueOnce({ message: 'Epic account connected!' })
      mockGet.mockResolvedValueOnce({ enabled: true, connected: true })

      const store = useDataStore()
      await store.submitEpicCode('epic_work', '{"authorizationCode":"x"}')

      expect(mockPost).toHaveBeenCalledWith(
        '/epic/exchange',
        { code_or_json: '{"authorizationCode":"x"}' },
        { source_id: 'epic_work' },
      )
      expect(mockGet).toHaveBeenCalledWith('/epic/status', { source_id: 'epic_work' })
      expect(store.oauthMessages['epic_work']).toBe('Epic account connected!')
    })

    it('submitGogCode surfaces an API error against that source alone', async () => {
      mockPost.mockRejectedValueOnce(new ApiError(500, 'Internal Server Error'))

      const store = useDataStore()
      await store.submitGogCode('gog_work', 'auth-code')

      expect(store.oauthMessages['gog_work']).toBe(
        'Error: 500 Internal Server Error',
      )
      expect(store.oauthMessages['gog']).toBeUndefined()
      expect(mockGet).not.toHaveBeenCalled()
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

    it('disconnectEpic deletes that source token and re-reads its status', async () => {
      mockDelete.mockResolvedValue({})
      mockGet.mockResolvedValueOnce({ enabled: true, connected: false })

      const store = useDataStore()
      await store.disconnectEpic('epic_work')

      expect(mockDelete).toHaveBeenCalledWith('/epic/token', {
        source_id: 'epic_work',
      })
      expect(store.oauthMessages['epic_work']).toBe(
        'Disconnected. You can reconnect below.',
      )
    })

    it('disconnectGog surfaces API error and does not re-read status', async () => {
      mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

      const store = useDataStore()
      await store.disconnectGog('gog_work')

      expect(store.oauthMessages['gog_work']).toBe(
        'Error: 500 Internal Server Error',
      )
      // The re-read must only run on success — otherwise a failed disconnect
      // triggers a spurious status fetch that itself may error.
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('disconnectGog keeps the confirmation and rejects when the re-read fails', async () => {
      mockDelete.mockResolvedValueOnce({})
      mockGet.mockRejectedValueOnce(new ApiError(503, 'Service Unavailable'))

      const store = useDataStore()

      // Swallowed, this left the panel claiming the account was connected —
      // Disconnect button and all — beside the message saying it was not.
      await expect(store.disconnectGog('gog_work')).rejects.toBeInstanceOf(
        ApiError,
      )
      // The credential is already gone; a stale panel is not a failed
      // disconnect and must not be reported as one.
      expect(store.oauthMessages['gog_work']).toBe(
        'Disconnected. You can reconnect below.',
      )
    })

    it('disconnectEpic surfaces API error and does not re-read status', async () => {
      mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

      const store = useDataStore()
      await store.disconnectEpic('epic_work')

      expect(store.oauthMessages['epic_work']).toBe(
        'Error: 500 Internal Server Error',
      )
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('disconnectGog surfaces generic error with fallback message', async () => {
      mockDelete.mockRejectedValue(new Error('network timeout'))

      const store = useDataStore()
      await store.disconnectGog('gog_work')

      expect(store.oauthMessages['gog_work']).toBe('Error: disconnect failed')
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('disconnectEpic surfaces generic error with fallback message', async () => {
      mockDelete.mockRejectedValue(new Error('network timeout'))

      const store = useDataStore()
      await store.disconnectEpic('epic_work')

      expect(store.oauthMessages['epic_work']).toBe('Error: disconnect failed')
      expect(mockGet).not.toHaveBeenCalled()
    })

    it('disconnectGog sets in-progress message before awaiting DELETE', async () => {
      let rejectDelete: (err: Error) => void = () => {}
      mockDelete.mockImplementation(
        () => new Promise((_, reject) => { rejectDelete = reject })
      )

      const store = useDataStore()
      const pending = store.disconnectGog('gog_work')
      // Synchronous assignment must land before the promise resolves so the
      // aria-live region announces activity immediately.
      expect(store.oauthMessages['gog_work']).toBe('Disconnecting GOG...')

      rejectDelete(new ApiError(500, 'Internal Server Error'))
      await pending

      expect(store.oauthMessages['gog_work']).toBe(
        'Error: 500 Internal Server Error',
      )
    })

    it('disconnectEpic sets in-progress message before awaiting DELETE', async () => {
      let rejectDelete: (err: Error) => void = () => {}
      mockDelete.mockImplementation(
        () => new Promise((_, reject) => { rejectDelete = reject })
      )

      const store = useDataStore()
      const pending = store.disconnectEpic('epic_work')
      expect(store.oauthMessages['epic_work']).toBe('Disconnecting Epic Games...')

      rejectDelete(new ApiError(500, 'Internal Server Error'))
      await pending

      expect(store.oauthMessages['epic_work']).toBe(
        'Error: 500 Internal Server Error',
      )
    })
  })

  describe('trakt device-code auth', () => {
    it('startTraktFlow POSTs for the named source and returns the payload', async () => {
      const flow = {
        user_code: 'ABCD-1234',
        verification_url: 'https://trakt.tv/activate',
        device_code: 'dev-code',
        expires_in: 600,
        interval: 5,
      }
      mockPost.mockResolvedValueOnce(flow)

      const store = useDataStore()
      const result = await store.startTraktFlow('trakt_work')

      expect(mockPost).toHaveBeenCalledWith(
        '/trakt/start-device-flow',
        undefined,
        { source_id: 'trakt_work' },
      )
      expect(result).toEqual(flow)
    })

    it('pollTraktApproval returns pending without re-reading status', async () => {
      mockPost.mockResolvedValueOnce({
        connected: false,
        status: 'pending',
        message: 'Waiting',
      })

      const store = useDataStore()
      const result = await store.pollTraktApproval('trakt_work', 'dev-code')

      expect(mockPost).toHaveBeenCalledWith(
        '/trakt/poll-device-approval',
        { device_code: 'dev-code' },
        { source_id: 'trakt_work' },
      )
      expect(result.connected).toBe(false)
      expect(store.oauthStatusFor('trakt_work').connected).toBe(false)
      expect(mockGet).not.toHaveBeenCalled()
    })

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

    it('pollTraktApproval falls back to a default confirmation message', async () => {
      mockPost.mockResolvedValueOnce({ connected: true, message: '' })
      mockGet.mockResolvedValueOnce({ enabled: true, connected: true })

      const store = useDataStore()
      await store.pollTraktApproval('trakt_work', 'dev-code')

      expect(store.oauthMessages['trakt_work']).toBe('Trakt account connected.')
    })

    it('pollTraktApproval keeps the confirmation when the status re-read fails', async () => {
      mockPost.mockResolvedValueOnce({ connected: true, message: 'Connected' })
      mockGet.mockRejectedValueOnce(new ApiError(503, 'Service Unavailable'))

      const store = useDataStore()
      const result = await store.pollTraktApproval('trakt_work', 'dev-code')

      // The token is stored; the flow must not report the connect as failed.
      expect(result.connected).toBe(true)
      expect(store.oauthMessages['trakt_work']).toBe('Connected')
    })

    it('pollTraktApproval surfaces the expired terminal status', async () => {
      mockPost.mockResolvedValueOnce({
        connected: false,
        status: 'expired',
        message: 'Code expired',
      })

      const store = useDataStore()
      const result = await store.pollTraktApproval('trakt_work', 'dev-code')

      expect(result.status).toBe('expired')
      expect(store.oauthStatusFor('trakt_work').connected).toBe(false)
    })

    it('pollTraktApproval surfaces the denied terminal status', async () => {
      mockPost.mockResolvedValueOnce({
        connected: false,
        status: 'denied',
        message: 'Denied',
      })

      const store = useDataStore()
      const result = await store.pollTraktApproval('trakt_work', 'dev-code')

      expect(result.status).toBe('denied')
      expect(store.oauthStatusFor('trakt_work').connected).toBe(false)
    })

    it('disconnectTrakt DELETEs that source token and re-reads its status', async () => {
      mockDelete.mockResolvedValueOnce({})
      mockGet.mockResolvedValueOnce({ enabled: true, connected: false })

      const store = useDataStore()
      await store.disconnectTrakt('trakt_work')

      expect(mockDelete).toHaveBeenCalledWith('/trakt/token', {
        source_id: 'trakt_work',
      })
      expect(store.oauthStatusFor('trakt_work').connected).toBe(false)
      expect(store.oauthMessages['trakt_work']).toBe(
        'Disconnected. You can reconnect below.',
      )
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

    it('startTraktFlow propagates a 400 not-configured rejection to the caller', async () => {
      const error = new ApiError(400, 'Bad Request')
      mockPost.mockRejectedValueOnce(error)

      const store = useDataStore()

      await expect(store.startTraktFlow('trakt_work')).rejects.toBe(error)
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

    it('disconnectTrakt announces the attempt before awaiting the DELETE', async () => {
      let rejectDelete: (err: Error) => void = () => {}
      mockDelete.mockImplementation(
        () => new Promise((_, reject) => { rejectDelete = reject })
      )

      const store = useDataStore()
      const pending = store.disconnectTrakt('trakt_work')
      expect(store.oauthMessages['trakt_work']).toBe('Disconnecting Trakt...')

      rejectDelete(new Error('network timeout'))
      await pending

      expect(store.oauthMessages['trakt_work']).toBe('Error: disconnect failed')
    })
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
          plugin_not_loaded: null,
        },
      ]

      await store.setSourceEnabled('steam', false)

      expect(store.syncSources[0].enabled).toBe(false)
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
          plugin_not_loaded: null,
        },
        {
          id: 'survivor',
          display_name: 'Survivor',
          plugin_display_name: 'Fake File',
          enabled: true,
          plugin_not_loaded: null,
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
          plugin_not_loaded: null,
        },
      ]

      await expect(store.deleteSource('still_here')).rejects.toBe(error)
      // The listing is untouched so the UI keeps showing the source.
      expect(store.syncSources.map((s) => s.id)).toEqual(['still_here'])
    })
  })
})
