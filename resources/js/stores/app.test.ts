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

/** Longer than any wait for a backend still coming up, shorter than the idle
 *  version check, so it separates the two cadences without naming either. */
const A_MINUTE = 60_000
/** Long enough that the idle version check has had to come round on its own. */
const AN_HOUR = 3_600_000

function answer(status: string, version = '1.2.3') {
  return {
    status,
    version,
    components: {},
    recommendations_config: { max_count: 20, default_count: 5 },
  }
}

describe('useAppStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    mockGet.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
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
    mockGet.mockResolvedValue(answer('initializing'))

    const store = useAppStore()
    await store.fetchStatus()

    expect(store.status).toBe('loading')
    expect(store.statusMessage).toBe('System initializing...')
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
    mockGet.mockResolvedValue(answer('ready'))

    const store = useAppStore()
    await store.fetchStatus()

    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(mockGet).toHaveBeenCalledWith('/status')
  })

  const UNREADY: Array<{ boot: string; first: () => Promise<unknown> }> = [
    { boot: 'a backend that was still starting up', first: () => Promise.resolve(answer('initializing')) },
    { boot: 'a server that did not answer at all', first: () => Promise.reject(new Error('unreachable')) },
  ]

  // Regression: status was read once at boot and never again, so a UI opened
  // against $boot carried its bar for the whole session, with a reload the only
  // way out and nothing on screen saying so.
  it.each(UNREADY)('clears the bar by itself after $boot comes good', async ({ first }) => {
    mockGet.mockImplementationOnce(first).mockResolvedValue(answer('ready'))

    const store = useAppStore()
    await store.fetchStatus()
    expect(store.statusMessage).not.toBe('')

    await vi.advanceTimersByTimeAsync(A_MINUTE)

    expect(store.status).toBe('ready')
    expect(store.statusMessage).toBe('')
  })

  it('asks nothing further once the backend has answered ready', async () => {
    mockGet.mockResolvedValue(answer('ready'))

    const store = useAppStore()
    await store.fetchStatus()
    const asked = mockGet.mock.calls.length

    await vi.advanceTimersByTimeAsync(A_MINUTE)

    expect(mockGet).toHaveBeenCalledTimes(asked)
  })

  it('stops polling when told to, so a signed-out tab holds no timer', async () => {
    mockGet.mockResolvedValue(answer('initializing'))

    const store = useAppStore()
    await store.fetchStatus()
    store.stopPolling()
    const asked = mockGet.mock.calls.length

    await vi.advanceTimersByTimeAsync(A_MINUTE)

    expect(mockGet).toHaveBeenCalledTimes(asked)
  })

  // Regression: taking "stop once ready" literally takes the version watch with
  // it, and a tab left open all day never learns it is running an old build.
  it('raises the update banner for a version deployed while the tab sat idle', async () => {
    mockGet
      .mockResolvedValueOnce(answer('ready', '1.2.3'))
      .mockResolvedValue(answer('ready', '1.3.0'))

    const store = useAppStore()
    await store.fetchStatus()
    expect(store.showUpdateBanner).toBe(false)

    await vi.advanceTimersByTimeAsync(AN_HOUR)

    expect(store.showUpdateBanner).toBe(true)
  })
})
