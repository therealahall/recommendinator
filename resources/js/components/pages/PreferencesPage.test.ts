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

// Symptom: a failed preferences GET rendered the form at its hardcoded
// defaults, and one Save wiped the stored preferences. Fix: the form renders
// only off server values, and a failure says so and offers a retry.
describe('PreferencesPage load failure', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
    mockPreferencesGet.mockReset()
    withTheme()
  })

  async function mountFailed() {
    mockPreferencesGet.mockRejectedValue(new Error('boom'))
    const wrapper = mount(PreferencesPage)
    await flushPromises()
    return wrapper
  }

  it('shows an error with a Retry that reloads, not the form', async () => {
    const wrapper = await mountFailed()

    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Couldn't load preferences",
    )
    expect(wrapper.findAll('button').map((b) => b.text())).not.toContain(
      'Save Preferences',
    )

    mockPreferencesGet.mockResolvedValue(PREFERENCES)
    await wrapper.find('[data-testid="preferences-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAll('button').map((b) => b.text())).toContain(
      'Save Preferences',
    )
  })

  // Regression: clicking Retry cleared loadError, the error branch gave way to
  // "Loading preferences…", and the button holding focus was unmounted —
  // dropping the keyboard user to <body> (WCAG 2.4.3).
  it('keeps focus on Retry while the reload it started is in flight', async () => {
    mockPreferencesGet.mockRejectedValue(new Error('boom'))
    const wrapper = mount(PreferencesPage, { attachTo: document.body })
    await flushPromises()

    let settleGet: (value: unknown) => void = () => {}
    mockPreferencesGet.mockReturnValue(
      new Promise((resolve) => {
        settleGet = resolve
      }),
    )
    const retry = wrapper.get('[data-testid="preferences-retry"]')
    ;(retry.element as HTMLButtonElement).focus()
    await retry.trigger('click')
    await wrapper.vm.$nextTick()

    expect(retry.text()).toBe('Retrying…')
    expect(retry.attributes('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(retry.element)

    settleGet(PREFERENCES)
    await flushPromises()
    wrapper.unmount()
  })

  it('keeps the Retry button out of the alert region', async () => {
    // Alert content is announced as one chunk, so a button inside it has its
    // affordance buried in the error prose.
    const wrapper = await mountFailed()

    const alert = wrapper.find('[role="alert"]')
    expect(alert.find('[data-testid="preferences-retry"]').exists()).toBe(false)
    // Defaults to type="submit" without this, which would post a wrapping form.
    expect(
      wrapper.find('[data-testid="preferences-retry"]').attributes('type'),
    ).toBe('button')
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
