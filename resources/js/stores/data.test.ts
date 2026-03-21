import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    raw: vi.fn(),
  }),
}))

describe('useDataStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockGet.mockReset()
    mockPost.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('has correct initial state', () => {
    const store = useDataStore()
    expect(store.syncSources).toEqual([])
    expect(store.syncStatus).toBe('idle')
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

  it('triggerSync starts sync and sets message', async () => {
    mockPost.mockResolvedValue({ message: 'Sync started for steam' })
    mockGet.mockResolvedValue({ status: 'idle' })

    const store = useDataStore()
    await store.triggerSync('steam')

    expect(store.syncMessage).toBe('Sync started for steam')
    expect(store.syncStatus).toBe('running')
  })

  it('checkSyncStatus updates status from API', async () => {
    mockGet.mockResolvedValue({
      status: 'completed',
      job: { items_processed: 42, error_count: 0, source: 'steam' },
    })

    const store = useDataStore()
    await store.checkSyncStatus()

    expect(store.syncStatus).toBe('completed')
    expect(store.syncMessage).toContain('42 items synced')
  })

  it('loadEnrichmentStats fetches stats', async () => {
    const stats = { enabled: true, total: 100, enriched: 50, pending: 30, not_found: 10, failed: 10, by_provider: {}, by_quality: {} }
    mockGet.mockResolvedValue(stats)

    const store = useDataStore()
    await store.loadEnrichmentStats()

    expect(store.enrichmentStats).toEqual(stats)
    expect(store.enrichmentEnabled).toBe(true)
  })
})
