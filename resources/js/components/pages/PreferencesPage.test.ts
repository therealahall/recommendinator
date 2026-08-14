import { describe, it, expect, vi, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreferencesPage from './PreferencesPage.vue'
import { useThemeStore } from '@/stores/theme'
import { usePreferencesStore } from '@/stores/preferences'

const PROFILE = {
  user_id: 1,
  genre_affinities: { 'science fiction': 0.9 },
  theme_preferences: [],
  anti_preferences: ['horror'],
  cross_media_patterns: ['Reads the book before the film'],
  generated_at: '2026-01-01T00:00:00+00:00',
}

const mockPost = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn((path: string) =>
      Promise.resolve(
        path === '/profile'
          ? PROFILE
          : {
              scorer_weights: {},
              series_in_order: true,
              variety_penalty: 0.0,
              content_length_preferences: {},
              custom_rules: [],
              theme: 'nord',
            },
      ),
    ),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn().mockResolvedValue({}),
  }),
}))

describe('PreferencesPage information architecture', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
  })

  it('renders the heading outline Appearance -> Scoring -> Rules -> Your Profile with sub-blocks', async () => {
    // Appearance only renders when themes are available.
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.findAll('h2').map((h) => h.text())).toEqual(['Preferences'])
    expect(wrapper.findAll('h3').map((h) => h.text())).toEqual([
      'Appearance',
      'Scoring',
      'Rules',
      'Your Profile',
    ])
    expect(wrapper.findAll('h4').map((h) => h.text())).toEqual([
      'Length',
      'Custom rules',
      'Genres You Love',
      'Not Your Style',
      'Patterns',
    ])
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

  it('marks the preferences card aria-busy while loading, then clears it', async () => {
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    const prefs = usePreferencesStore()
    await flushPromises()

    prefs.loading = true
    await nextTick()
    expect(wrapper.find('.card').attributes('aria-busy')).toBe('true')

    prefs.loading = false
    await nextTick()
    expect(wrapper.find('.card').attributes('aria-busy')).toBeUndefined()
  })

  it('no longer renders a "Toggles" section', async () => {
    const theme = useThemeStore()
    theme.themes = [
      { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
    ]

    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.text()).not.toContain('Toggles')
  })
})
