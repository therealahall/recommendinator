import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PreferencesPage from './PreferencesPage.vue'
import { useThemeStore } from '@/stores/theme'

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

/** Register the one theme the Appearance section needs to render. */
function withTheme() {
  useThemeStore().themes = [
    { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
  ]
}

describe('PreferencesPage information architecture', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
    mockPreferencesGet.mockReset()
    mockPreferencesGet.mockResolvedValue(PREFERENCES)
  })

  it('renders the heading outline Appearance -> Scoring -> Rules -> Your Profile with sub-blocks', async () => {
    // Appearance only renders when themes are available.
    withTheme()

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

  it('no longer renders a "Toggles" section', async () => {
    withTheme()

    const wrapper = mount(PreferencesPage)
    await flushPromises()

    expect(wrapper.text()).not.toContain('Toggles')
  })
})

// The busy flag has to live on the page wrapper: it is the only node present in
// every outcome, so it is the only one that can flip rather than unmount (4.1.3).
describe.each([
  { outcome: 'preferences arrive', settle: PREFERENCES, shows: 'Save Preferences' },
  { outcome: 'the load fails', settle: new Error('boom'), shows: "Couldn't load preferences" },
])('PreferencesPage aria-busy when $outcome', ({ settle, shows }) => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
    mockPreferencesGet.mockReset()
    withTheme()
  })

  it('clears in place on the page wrapper', async () => {
    let settleGet: () => void = () => {}
    mockPreferencesGet.mockReturnValue(
      new Promise((resolve, reject) => {
        settleGet = () =>
          settle instanceof Error ? reject(settle) : resolve(settle)
      }),
    )
    const wrapper = mount(PreferencesPage)
    await flushPromises()

    const busy = wrapper.find('[aria-busy="true"]')
    expect(busy.element).toBe(wrapper.element)

    settleGet()
    await flushPromises()

    expect(busy.attributes('aria-busy')).toBeUndefined()
    expect(wrapper.text()).toContain(shows)
  })
})
