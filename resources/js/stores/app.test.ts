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
    raw: vi.fn(),
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
    expect(store.users).toEqual([])
    expect(store.version).toBe('')
    expect(store.showUpdateBanner).toBe(false)
    expect(store.features.ai_enabled).toBe(false)
  })

  it('fetchStatus sets state on success', async () => {
    mockGet.mockResolvedValue({
      status: 'ready',
      version: '1.2.3',
      components: {},
      features: { ai_enabled: true, embeddings_enabled: true, llm_reasoning_enabled: false },
      recommendations_config: { max_count: 20, default_count: 10 },
    })

    const store = useAppStore()
    await store.fetchStatus()

    expect(store.status).toBe('ready')
    expect(store.statusMessage).toBe('')
    expect(store.version).toBe('1.2.3')
    expect(store.features.ai_enabled).toBe(true)
    expect(store.recommendationsConfig.default_count).toBe(10)
  })

  it('fetchStatus sets initializing message when not ready', async () => {
    mockGet.mockResolvedValue({
      status: 'loading',
      version: '',
      components: {},
      features: { ai_enabled: false, embeddings_enabled: false, llm_reasoning_enabled: false },
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
      features: { ai_enabled: false, embeddings_enabled: false, llm_reasoning_enabled: false },
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

  it('fetchUsers populates users list', async () => {
    const mockUsers = [
      { id: 1, username: 'alice', display_name: 'Alice' },
      { id: 2, username: 'bob', display_name: null },
    ]
    mockGet.mockResolvedValue(mockUsers)

    const store = useAppStore()
    await store.fetchUsers()

    expect(store.users).toEqual(mockUsers)
  })

  it('setUser updates currentUserId', () => {
    const store = useAppStore()
    store.setUser(42)
    expect(store.currentUserId).toBe(42)
  })

  it('chatEnabled computed depends on ai_enabled', () => {
    const store = useAppStore()
    expect(store.chatEnabled).toBe(false)
    store.features.ai_enabled = true
    expect(store.chatEnabled).toBe(true)
  })

  it('aiReasoningEnabled requires both ai and llm_reasoning', () => {
    const store = useAppStore()
    expect(store.aiReasoningEnabled).toBe(false)
    store.features.ai_enabled = true
    expect(store.aiReasoningEnabled).toBe(false)
    store.features.llm_reasoning_enabled = true
    expect(store.aiReasoningEnabled).toBe(true)
  })

  it('currentUser returns matching user', () => {
    const store = useAppStore()
    store.users = [
      { id: 1, username: 'alice', display_name: 'Alice' },
      { id: 2, username: 'bob', display_name: null },
    ]
    store.currentUserId = 2
    expect(store.currentUser?.username).toBe('bob')
  })
})
