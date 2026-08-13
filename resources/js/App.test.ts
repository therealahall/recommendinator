import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import LoginForm from '@/components/organisms/LoginForm.vue'
import SetupForm from '@/components/organisms/SetupForm.vue'
import { ApiError, useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import { SESSION_ENDED, useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { deferredFetch, jsonResponse } from '@/testing/http'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = { id: 1, username: 'aaron', display_name: 'Aaron Hall' }

/** Neutralise the calls App fires once the session resolves, so they can be
 *  counted rather than performed. */
function spyOnLoad() {
  const app = useAppStore()
  const theme = useThemeStore()
  return {
    fetchStatus: vi.spyOn(app, 'fetchStatus').mockResolvedValue(undefined),
    fetchThemes: vi.spyOn(theme, 'fetchThemes').mockResolvedValue(undefined),
    applyStoredTheme: vi.spyOn(theme, 'applyStoredTheme').mockImplementation(() => {}),
  }
}

function answerSession(claimed: boolean, authenticated: boolean, user: UserResponse | null = null) {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { claimed, authenticated, user }))
}

function sessionCalls(): number {
  return vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === '/api/auth/session').length
}

describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders no screen, and fetches nothing, until the session resolves', async () => {
    const load = spyOnLoad()
    deferredFetch()

    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    expect(wrapper.findComponent(SetupForm).exists()).toBe(false)
    expect(wrapper.findComponent(LoginForm).exists()).toBe(false)
    expect(wrapper.find('#main-content').exists()).toBe(false)
    expect(load.fetchStatus).not.toHaveBeenCalled()
    expect(load.fetchThemes).not.toHaveBeenCalled()
  })

  it('applies the stored theme before the session is known', async () => {
    // Themes are static files behind no session, and someone who picked the
    // light theme because they cannot read light-on-dark cannot skip sign-in.
    const load = spyOnLoad()
    deferredFetch()

    mount(App, { shallow: true })
    await flushPromises()

    expect(load.applyStoredTheme).toHaveBeenCalledTimes(1)
  })

  const SCREENS: Array<{ instance: string; claimed: boolean; authenticated: boolean }> = [
    { instance: 'a fresh instance opens on setup', claimed: false, authenticated: false },
    { instance: 'a claimed instance opens on sign-in', claimed: true, authenticated: false },
    { instance: 'a signed-in instance opens the shell', claimed: true, authenticated: true },
  ]

  it.each(SCREENS)('$instance, decided by one session call', async ({ claimed, authenticated }) => {
    spyOnLoad()
    answerSession(claimed, authenticated, authenticated ? AARON : null)

    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    expect(wrapper.findComponent(SetupForm).exists()).toBe(!claimed)
    expect(wrapper.findComponent(LoginForm).exists()).toBe(claimed && !authenticated)
    expect(wrapper.find('#main-content').exists()).toBe(authenticated)

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe('/api/auth/session')
  })

  it('loads the app exactly once, when the session comes back signed in', async () => {
    const load = spyOnLoad()
    answerSession(true, true, AARON)

    mount(App, { shallow: true })
    await flushPromises()

    expect(load.fetchStatus).toHaveBeenCalledTimes(1)
    expect(load.fetchThemes).toHaveBeenCalledTimes(1)
  })

  it('puts the sign-in screen back when the session is revoked mid-session', async () => {
    // Regression: a credential revoked while the app was open surfaced as a 401
    // each store swallowed, leaving a half-empty shell and no way to sign in
    // again. reject() has to take the whole shell down with it.
    spyOnLoad()
    answerSession(true, true, AARON)
    const wrapper = mount(App, { shallow: true })
    await flushPromises()
    expect(wrapper.find('#main-content').exists()).toBe(true)

    useAuthStore().reject()
    await flushPromises()

    expect(wrapper.findComponent(LoginForm).exists()).toBe(true)
    expect(wrapper.find('#main-content').exists()).toBe(false)
    // A shell that empties with no word reads as a crash.
    expect(wrapper.findComponent(LoginForm).props('error')).toBe(SESSION_ENDED)
  })

  it('puts it back when a request comes back 401, without reloading the page', async () => {
    // The whole point of the store handling the refusal: a reload would be the
    // only other way back, and it would lose whatever the user was doing.
    spyOnLoad()
    answerSession(true, true, AARON)
    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(401, { detail: 'Not signed in.' }))
    await expect(useApi().get('/library')).rejects.toBeInstanceOf(ApiError)
    await flushPromises()

    expect(wrapper.findComponent(LoginForm).exists()).toBe(true)
    expect(wrapper.findComponent(LoginForm).props('error')).toBe(SESSION_ENDED)
    // Booting again is what a reload looks like from here.
    expect(sessionCalls()).toBe(1)
  })

  it('returns to the sign-in screen after signing out', async () => {
    spyOnLoad()
    answerSession(true, true, AARON)
    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
    await useAuthStore().signOut()
    await flushPromises()

    expect(wrapper.findComponent(LoginForm).exists()).toBe(true)
    expect(wrapper.find('#main-content').exists()).toBe(false)
  })

  it('takes the next boot from the server, not from anything it kept', async () => {
    // The reload after a sign-out: the cookie is gone, and there is nothing on
    // the page that could put the shell back up without it.
    spyOnLoad()
    answerSession(true, true, AARON)
    const first = mount(App, { shallow: true })
    await flushPromises()
    expect(first.find('#main-content').exists()).toBe(true)
    first.unmount()

    setActivePinia(createPinia())
    spyOnLoad()
    answerSession(true, false)
    const reloaded = mount(App, { shallow: true })
    await flushPromises()

    expect(reloaded.findComponent(LoginForm).exists()).toBe(true)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('clears the password on a second refusal worded exactly like the first', async () => {
    // The two refusals are the same string, so a form watching the message for
    // a change sees none and leaves the wrong password in the field.
    spyOnLoad()
    answerSession(true, false)
    const wrapper = mount(App)
    await flushPromises()

    const refused = jsonResponse(401, { detail: 'That username and password do not match an account.' })
    vi.mocked(fetch).mockResolvedValue(refused)
    await wrapper.find('#login-username').setValue('aaron')

    for (const attempt of ['first-guess', 'second-guess']) {
      await wrapper.find('#login-password').setValue(attempt)
      await wrapper.find('form').trigger('submit')
      await flushPromises()

      expect(wrapper.find<HTMLInputElement>('#login-password').element.value).toBe('')
      expect(wrapper.find<HTMLInputElement>('#login-username').element.value).toBe('aaron')
    }
  })

  it('moves focus to the main landmark when the shell replaces the sign-in screen', async () => {
    // The sign-in screen takes the focused input with it, so focus falls back to
    // <body> and the next Tab restarts from the sidebar.
    spyOnLoad()
    answerSession(true, false)
    const wrapper = mount(App, { shallow: true, attachTo: document.body })
    await flushPromises()

    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, AARON))
    await useAuthStore().signIn({ username: 'aaron', password: 'hunter2' })
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.find('#main-content').element)
    wrapper.unmount()
  })
})
