import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import PreferencesPage from './PreferencesPage.vue'

const PROFILE = {
  user_id: 1,
  genre_affinities: { 'science fiction': 0.9 },
  theme_preferences: [],
  anti_preferences: ['horror'],
  cross_media_patterns: ['Reads the book before the film'],
  generated_at: '2026-01-01T00:00:00+00:00',
}

/** A fresh response each call: the store keeps the collections it is handed. */
function preferences(customRules: string[] = []) {
  return {
    scorer_weights: {},
    series_in_order: true,
    variety_penalty: 0.0,
    content_length_preferences: {},
    custom_rules: customRules,
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
async function mountPage(attachTo?: HTMLElement) {
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
    { global: { plugins: [router] }, attachTo },
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
    // A theme applied here outlives the test that applied it.
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

  // Parity with `preferences reset`, which also confirms first.
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

  it('does not offer to clear the theme, which the reset leaves alone', async () => {
    const { wrapper } = await mountPage()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    await wrapper.findAll('button').find((b) => b.text() === 'Reset to defaults')!.trigger('click')

    expect(String(confirm.mock.calls[0][0])).not.toMatch(/theme/i)
  })

  // A button disabled mid-request is blurred by the browser, dropping the
  // keyboard user to <body> for the rest of the flight (WCAG 2.4.3).
  it('keeps Reset to defaults focusable while the reset is in flight', async () => {
    const { wrapper } = await mountPage(document.body)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let settle: (defaults: unknown) => void = () => {}
    mockDelete.mockImplementation(
      () => new Promise((resolve) => { settle = resolve }),
    )
    const reset = wrapper.findAll('button').find((b) => b.text() === 'Reset to defaults')!
    ;(reset.element as HTMLButtonElement).focus()

    await reset.trigger('click')

    expect(reset.attributes('disabled')).toBeUndefined()

    settle(preferences())
    await flushPromises()

    expect(document.activeElement).toBe(reset.element)
    wrapper.unmount()
  })

  it('does not send a second reset while the first is in flight', async () => {
    const { wrapper } = await mountPage()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDelete.mockImplementation(() => new Promise(() => {}))
    const reset = wrapper.findAll('button').find((b) => b.text() === 'Reset to defaults')!

    await reset.trigger('click')
    await reset.trigger('click')

    expect(mockDelete).toHaveBeenCalledTimes(1)
  })
})
