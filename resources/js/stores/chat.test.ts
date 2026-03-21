import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()
const mockRaw = vi.fn()

vi.mock('@/composables/useApi', () => ({
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
