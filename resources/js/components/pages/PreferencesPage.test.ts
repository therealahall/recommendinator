import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreferencesPage from './PreferencesPage.vue'

const PROFILE = {
  user_id: 1,
  genre_affinities: { 'science fiction': 0.9 },
  theme_preferences: [],
  anti_preferences: ['horror'],
  cross_media_patterns: ['Reads the book before the film'],
  generated_at: '2026-01-01T00:00:00+00:00',
}

const PREFERENCES = {
  scorer_weights: {},
  series_in_order: true,
  variety_penalty: 0.0,
  content_length_preferences: {},
  custom_rules: [],
  theme: 'nord',
}

const mockPost = vi.fn()
const mockPreferencesGet = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (path: string) =>
      path === '/profile' ? Promise.resolve(PROFILE) : mockPreferencesGet(path),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn().mockResolvedValue({}),
  }),
}))

describe('PreferencesPage information architecture', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
    mockPreferencesGet.mockReset()
    mockPreferencesGet.mockResolvedValue(PREFERENCES)
  })

  // The CLI keeps `profile show` and `profile regenerate`, so the web has to
  // reach both or the interfaces have drifted.
  it('shows the preference profile and regenerates it', async () => {
    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.text()).toContain('science fiction')

    mockPost.mockResolvedValue(PROFILE)
    await wrapper.findAll('button').find((b) => b.text() === 'Regenerate')!.trigger('click')

    expect(mockPost).toHaveBeenCalledWith('/profile/regenerate', expect.anything())
  })
})
