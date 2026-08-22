import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import PreferencesPage from './PreferencesPage.vue'
import { useThemeStore } from '@/stores/theme'

const THEMES = [
  { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
  { id: 'snowstorm', name: 'Snowstorm', description: '', author: '', version: '1.0.0', theme_type: 'light' },
]

const PROFILE = {
  user_id: 1,
  genre_affinities: { 'science fiction': 0.9 },
  theme_preferences: [],
  anti_preferences: ['horror'],
  cross_media_patterns: ['Reads the book before the film'],
  generated_at: '2026-01-01T00:00:00+00:00',
}

/** A fresh response each call: the store holds on to the collections it is
 *  handed, so a shared literal would carry one test's edits into the next. */
function preferences(customRules: string[] = []) {
  return {
    scorer_weights: {},
    series_in_order: true,
    variety_penalty: 0.0,
    content_length_preferences: {},
    custom_rules: customRules,
    theme: 'nord',
  }
}

const mockPost = vi.fn()
const mockDelete = vi.fn()
const mockPreferencesGet = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (path: string) =>
      path === '/profile' ? Promise.resolve(PROFILE) : mockPreferencesGet(path),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn().mockResolvedValue({}),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

/** The page inside a router, which is what makes its leave guard run. */
async function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/preferences', component: PreferencesPage },
      { path: '/library', component: { template: '<div>Library</div>' } },
    ],
  })
  await router.push('/preferences')
  await router.isReady()
  const wrapper = mount(
    { template: '<router-view />' },
    { global: { plugins: [router] } },
  )
  await flushPromises()
  return { wrapper, router }
}

async function addRule(wrapper: ReturnType<typeof mount>, text: string) {
  await wrapper.find('#new-rule-input').setValue(text)
  await wrapper.find('.add-rule-form button').trigger('click')
}

describe('PreferencesPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockPost.mockReset()
    mockDelete.mockReset()
    mockPreferencesGet.mockReset()
    mockPreferencesGet.mockImplementation(() => Promise.resolve(preferences()))
    // A theme applied here outlives the test in both, so without this the
    // browser one test leaves behind is the browser the next one starts in.
    localStorage.clear()
    document.getElementById('theme-stylesheet')?.remove()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // The CLI keeps `profile show` and `profile regenerate`, so the web has to
  // reach both or the interfaces have drifted.
  it('shows the preference profile and regenerates it', async () => {
    const { wrapper } = await mountPage()

    expect(wrapper.text()).toContain('science fiction')

    mockPost.mockResolvedValue(PROFILE)
    await wrapper.findAll('button').find((b) => b.text() === 'Regenerate')!.trigger('click')

    expect(mockPost).toHaveBeenCalledWith('/profile/regenerate', expect.anything())
  })

  // Nothing on this page is written until Save, so a nav link used to discard
  // a typed-in rule without a word.
  it('asks before a nav link discards an unsaved rule, and stays put', async () => {
    const { wrapper, router } = await mountPage()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await addRule(wrapper, 'prefer westerns')

    await router.push('/library')

    expect(confirm).toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/preferences')

    confirm.mockReturnValue(true)
    await router.push('/library')

    expect(router.currentRoute.value.path).toBe('/library')
  })

  // The prompt is the only warning a keyboard away from the page; on the page
  // itself nothing but this line says the buffer is unsaved.
  it('says the page is holding changes until they are saved', async () => {
    const { wrapper } = await mountPage()
    const marker = () => wrapper.find('[data-testid="preferences-dirty"]')

    expect(marker().exists()).toBe(false)

    await addRule(wrapper, 'prefer westerns')

    expect(marker().text()).not.toBe('')

    await wrapper.findAll('button').find((b) => b.text() === 'Save Preferences')!.trigger('click')
    await flushPromises()

    expect(marker().exists()).toBe(false)
  })

  it('does not ask when an edit was put back the way it was', async () => {
    const { wrapper, router } = await mountPage()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    await wrapper.find('#length-pref-book').setValue('short')
    await wrapper.find('#length-pref-book').setValue('any')
    await router.push('/library')

    expect(confirm).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/library')
  })

  // Parity with `preferences reset`, which also confirms before it throws the
  // weights, rules and length preferences away.
  it('confirms a reset, then shows the defaults it stored', async () => {
    mockPreferencesGet.mockImplementation(() =>
      Promise.resolve(preferences(['prefer westerns'])),
    )
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('prefer westerns')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const reset = wrapper.findAll('button').find((b) => b.text() === 'Reset to defaults')!
    await reset.trigger('click')

    expect(mockDelete).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    mockDelete.mockImplementation(() => Promise.resolve(preferences()))
    await reset.trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith('/users/1/preferences')
    expect(wrapper.text()).not.toContain('prefer westerns')
  })

  // The theme is stored server-side to reach a second device, and boot applies
  // the config default before any page loads, so load's "this browser has none
  // yet" guard is already false when it is asked.
  it('applies the stored theme on a browser that has only the config default', async () => {
    mockPreferencesGet.mockImplementation((path: string) => {
      if (path === '/themes') return Promise.resolve(THEMES)
      if (path === '/themes/default') return Promise.resolve({ theme: 'nord' })
      return Promise.resolve({ ...preferences(), theme: 'snowstorm' })
    })
    const theme = useThemeStore()
    await theme.fetchThemes()

    await mountPage()

    const link = document.getElementById('theme-stylesheet') as HTMLLinkElement
    expect(link.href).toContain('/static/themes/snowstorm/colors.css')
  })
})
