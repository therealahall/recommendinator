import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAppStore } from './app'

const mockGet = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

describe('useAppStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockGet.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('has correct initial state', () => {
    const store = useAppStore()
    expect(store.status).toBe('loading')
    expect(store.statusMessage).toBe('')
    expect(store.currentUserId).toBe(1)
    expect(store.version).toBe('')
    expect(store.showUpdateBanner).toBe(false)
  })

  it('fetchStatus sets state on success', async () => {
    mockGet.mockResolvedValue({
      status: 'ready',
      version: '1.2.3',
      components: {},
      recommendations_config: { max_count: 20, default_count: 10 },
    })

    const store = useAppStore()
    await store.fetchStatus()

    expect(store.status).toBe('ready')
    expect(store.statusMessage).toBe('')
    expect(store.version).toBe('1.2.3')
    expect(store.recommendationsConfig.default_count).toBe(10)
  })

  it('fetchStatus sets initializing message when not ready', async () => {
    mockGet.mockResolvedValue({
      status: 'loading',
      version: '',
      components: {},
      recommendations_config: { max_count: 20, default_count: 5 },
    })

    const store = useAppStore()
    await store.fetchStatus()

    expect(store.status).toBe('loading')
    expect(store.statusMessage).toBe('System initializing...')
  })

  it('dismissStatus clears statusMessage', async () => {
    mockGet.mockResolvedValue({
      status: 'loading',
      version: '',
      components: {},
      recommendations_config: { max_count: 20, default_count: 5 },
    })

    const store = useAppStore()
    await store.fetchStatus()
    expect(store.statusMessage).toBe('System initializing...')

    store.dismissStatus()
    expect(store.statusMessage).toBe('')
  })

  it('fetchStatus sets error on failure', async () => {
    mockGet.mockRejectedValue(new Error('Network error'))

    const store = useAppStore()
    await store.fetchStatus()

    expect(store.status).toBe('error')
    expect(store.statusMessage).toBe('Failed to connect to server')
  })

  it('asks for no user list, because there is nobody to switch to', async () => {
    // The signed-in account comes from the session now, and a second person
    // signs in with their own credentials rather than being switched to.
    mockGet.mockResolvedValue({
      status: 'ready',
      version: '1.2.3',
      components: {},
      recommendations_config: { max_count: 20, default_count: 5 },
    })

    const store = useAppStore()
    await store.fetchStatus()

    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(mockGet).toHaveBeenCalledWith('/status')
  })
})
