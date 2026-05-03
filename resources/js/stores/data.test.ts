import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDataStore } from './data'
import { ApiError } from '@/composables/useApi'

const mockGet = vi.fn()
const mockPost = vi.fn()
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
    expect(store.syncingSource).toBeNull()
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
})
