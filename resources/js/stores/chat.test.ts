import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat'
import { jsonResponse } from '@/testing/http'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()
const mockRaw = vi.fn()

// Only useApi is replaced: the store's error path runs the real body parse, so
// a stubbed one cannot make it pass.
vi.mock('@/composables/useApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useApi')>()),
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
    raw: (...args: unknown[]) => mockRaw(...args),
  }),
}))

vi.mock('@/composables/useSse', () => ({
  readSseStream: vi.fn(),
}))

describe('useChatStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
    mockRaw.mockReset()
  })

  it('has correct initial state', () => {
    const store = useChatStore()
    expect(store.messages).toEqual([])
    expect(store.isStreaming).toBe(false)
    expect(store.showWelcome).toBe(true)
    expect(store.memories).toEqual([])
    expect(store.profile).toBeNull()
  })

  it('loadMemories fetches from API', async () => {
    const memories = [
      { id: 1, memory_text: 'I like sci-fi', memory_type: 'user_stated', confidence: 1, is_active: true, source: 'user', created_at: '2024-01-01' },
    ]
    mockGet.mockResolvedValue(memories)

    const store = useChatStore()
    await store.loadMemories()

    expect(store.memories).toEqual(memories)
  })

  it('loadProfile fetches from API', async () => {
    const profile = {
      user_id: 1,
      genre_affinities: { 'sci-fi': 0.9 },
      theme_preferences: [],
      anti_preferences: ['horror'],
      cross_media_patterns: [],
      generated_at: null,
    }
    mockGet.mockResolvedValue(profile)

    const store = useChatStore()
    await store.loadProfile()

    expect(store.profile).toEqual(profile)
  })

  it('addMemory calls API and reloads', async () => {
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValue([])

    const store = useChatStore()
    await store.addMemory('I prefer short games')

    expect(mockPost).toHaveBeenCalledWith('/memories', expect.objectContaining({
      memory_text: 'I prefer short games',
    }))
  })

  it('toggleMemory toggles active state', async () => {
    mockPut.mockResolvedValue({})
    mockGet.mockResolvedValue([])

    const store = useChatStore()
    await store.toggleMemory(1, true)

    expect(mockPut).toHaveBeenCalledWith('/memories/1', { is_active: false })
  })

  it('deleteMemory removes memory', async () => {
    mockDelete.mockResolvedValue({})
    mockGet.mockResolvedValue([])

    const store = useChatStore()
    await store.deleteMemory(1)

    expect(mockDelete).toHaveBeenCalledWith('/memories/1')
  })

  it('shows what the server said when it refuses the stream', async () => {
    // Regression: every failed /chat became "Sorry, I encountered an error",
    // so the stream cap's 503 — the one refusal that tells the user how to get
    // unstuck — was discarded before anything could render it.
    mockRaw.mockResolvedValue(
      jsonResponse(503, { detail: 'Too many streams in progress. Try again in a moment.' }),
    )

    const store = useChatStore()
    await store.send('recommend me something')

    expect(store.messages[1]).toMatchObject({
      role: 'assistant',
      content: 'Too many streams in progress. Try again in a moment.',
    })
    expect(store.isStreaming).toBe(false)
  })

  it('keeps the generic apology when the request never reached the server', async () => {
    mockRaw.mockRejectedValue(new TypeError('Failed to fetch'))

    const store = useChatStore()
    await store.send('recommend me something')

    expect(store.messages[1]).toMatchObject({
      role: 'assistant',
      content: 'Sorry, I encountered an error. Please try again.',
    })
  })

  it('reset clears messages and shows welcome', async () => {
    mockPost.mockResolvedValue({})

    const store = useChatStore()
    store.messages = [{ id: 1, role: 'user', content: 'hello' }]
    store.showWelcome = false

    await store.reset()

    expect(store.messages).toEqual([])
    expect(store.showWelcome).toBe(true)
  })
})
