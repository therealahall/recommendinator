import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'
import { ApiError } from '@/composables/useApi'

const mockGet = vi.fn()
const mockPost = vi.fn()
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
    put: vi.fn(),
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
    mockDelete.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('has correct initial state', () => {
    const store = useDataStore()
    expect(store.syncSources).toEqual([])
    expect(store.syncStatus).toBe('idle')
    expect(store.syncingSource).toBeNull()
    expect(store.enrichmentStats).toBeNull()
  })

  it('loadSyncSources fetches sources and auth status', async () => {
    const sources = [{ id: 'steam', display_name: 'Steam', plugin_display_name: 'Steam' }]
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

  it('triggerSync sets syncingSource and status', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for steam' })

    const store = useDataStore()
    await store.triggerSync('steam')

    expect(store.syncMessage).toBe('Sync started for steam')
    expect(store.syncStatus).toBe('running')
    expect(store.syncingSource).toBe('steam')
  })

  it('triggerSync clears syncingSource on API error', async () => {
    mockPost.mockRejectedValue(new ApiError(503, 'Service Unavailable'))

    const store = useDataStore()
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.syncingSource).toBeNull()
    expect(store.syncMessage).toContain('server returned 503')
  })

  it('triggerSync clears syncingSource on generic error', async () => {
    mockPost.mockRejectedValue(new Error('network failure'))

    const store = useDataStore()
    await store.triggerSync('steam')

    expect(store.syncStatus).toBe('failed')
    expect(store.syncingSource).toBeNull()
    expect(store.syncMessage).toContain('unexpected failure')
  })

  it('checkSyncStatus clears syncingSource on completed', async () => {
    mockGet.mockResolvedValue({
      status: 'completed',
      job: { items_processed: 42, error_count: 0, source: 'steam' },
    })

    const store = useDataStore()
    store.$patch({ syncingSource: 'steam' })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('completed')
    expect(store.syncingSource).toBeNull()
    expect(store.syncMessage).toContain('42 items synced')
  })

  it('checkSyncStatus includes error count in completed message', async () => {
    mockGet.mockResolvedValue({
      status: 'completed',
      job: { items_processed: 100, error_count: 3, source: 'steam' },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('100 items synced')
    expect(store.syncMessage).toContain('3 errors')
  })

  it('checkSyncStatus clears syncingSource on failed', async () => {
    mockGet.mockResolvedValue({
      status: 'failed',
      job: { error_message: 'timeout', source: 'steam' },
    })

    const store = useDataStore()
    store.$patch({ syncingSource: 'steam' })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('failed')
    expect(store.syncingSource).toBeNull()
    expect(store.syncMessage).toContain('timeout')
  })

  it('checkSyncStatus clears syncingSource on idle', async () => {
    mockGet.mockResolvedValue({ status: 'idle' })

    const store = useDataStore()
    store.$patch({ syncingSource: 'steam' })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('idle')
    expect(store.syncingSource).toBeNull()
  })

  it('checkSyncStatus restores syncingSource from server on running', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: { source: 'goodreads', items_processed: 5, current_source: 'goodreads' },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('running')
    expect(store.syncingSource).toBe('goodreads')
  })

  it('checkSyncStatus builds progress message with total and percent', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: {
        source: 'steam', items_processed: 10, total_items: 100,
        progress_percent: 10, current_source: 'steam', current_item: 'Half-Life 2',
      },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('10/100')
    expect(store.syncMessage).toContain('(10%)')
    expect(store.syncMessage).toContain('Half-Life 2')
  })

  it('checkSyncStatus builds progress message at sync start', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: {
        source: 'steam', items_processed: 0, total_items: null,
        progress_percent: null, current_source: 'steam', current_item: null,
      },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('0 items so far')
    expect(store.syncMessage).not.toMatch(/^ *-/)
  })

  it('checkSyncStatus builds progress message with total but no percent', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: {
        source: 'steam', items_processed: 10, total_items: 100,
        progress_percent: null, current_source: 'steam', current_item: null,
      },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('10/100')
    expect(store.syncMessage).not.toContain('%')
  })

  it('checkSyncStatus treats total_items of 0 as unknown total', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: {
        source: 'steam', items_processed: 5, total_items: 0,
        progress_percent: null, current_source: 'steam', current_item: null,
      },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('5 items so far')
    expect(store.syncMessage).not.toContain('/0')
  })

  it('checkSyncStatus builds progress message with items only', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: {
        source: 'steam', items_processed: 7, total_items: null,
        progress_percent: null, current_source: 'steam', current_item: null,
      },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncMessage).toContain('7 items so far')
  })

  it('checkSyncStatus preserves syncingSource when already set and running', async () => {
    mockGet.mockResolvedValue({
      status: 'running',
      job: { source: 'steam', items_processed: 5, current_source: 'steam' },
    })

    const store = useDataStore()
    store.$patch({ syncingSource: 'all' })
    await store.checkSyncStatus()

    expect(store.syncingSource).toBe('all')
  })

  it('checkSyncStatus silently ignores GET failure without changing state', async () => {
    mockGet.mockRejectedValue(new Error('network error'))

    const store = useDataStore()
    store.$patch({ syncStatus: 'running', syncingSource: 'steam', syncMessage: 'previous message' })
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('running')
    expect(store.syncingSource).toBe('steam')
    expect(store.syncMessage).toBe('previous message')
  })

  it('loadEnrichmentStats fetches stats', async () => {
    const stats = { enabled: true, total: 100, enriched: 50, pending: 30, not_found: 10, failed: 10, by_provider: {}, by_quality: {} }
    mockGet.mockResolvedValue(stats)

    const store = useDataStore()
    await store.loadEnrichmentStats()

    expect(store.enrichmentStats).toEqual(stats)
    expect(store.enrichmentEnabled).toBe(true)
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
    expect(store.gogConnectMessage).toContain('Disconnected')
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
    expect(store.epicConnectMessage).toContain('Disconnected')
    expect(store.epicStatus.connected).toBe(false)
  })

  it('disconnectGog surfaces API error in the connect message', async () => {
    mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    const store = useDataStore()
    await store.disconnectGog()

    expect(store.gogConnectMessage).toBe('Error: server returned 500')
  })

  it('disconnectEpic surfaces API error in the connect message', async () => {
    mockDelete.mockRejectedValue(new ApiError(500, 'Internal Server Error'))

    const store = useDataStore()
    await store.disconnectEpic()

    expect(store.epicConnectMessage).toBe('Error: server returned 500')
  })
})
