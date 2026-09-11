import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useProfileStore } from './profile'

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  }),
}))

const AUTHORS_ONLY = {
  user_id: 1,
  genre_affinities: [],
  author_affinities: [{ author: 'Terry Brooks', score: 4.0 }],
  theme_preferences: [],
  cross_media_patterns: [],
  has_content: true,
  generated_at: '2026-08-13T12:00:00',
}

describe('useProfileStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
  })

  it('leaves the loaded profile on screen when a regenerate fails', async () => {
    mockGet.mockResolvedValue(AUTHORS_ONLY)
    mockPost.mockRejectedValue(new Error('Profile generation failed'))
    const store = useProfileStore()
    await store.load()

    await store.regenerate()

    expect(store.profile).toEqual(AUTHORS_ONLY)
    expect(store.error).toBe('Profile generation failed')
    expect(store.regenerating).toBe(false)
  })
})
