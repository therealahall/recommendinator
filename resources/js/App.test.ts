import { readFileSync } from 'node:fs'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
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

/** The two auth screens rendered for real, with the shell's own furniture
 *  stubbed: RouterView needs a router none of these tests provides. */
function mountWithShellStubs() {
  return mount(App, {
    global: { stubs: { RouterView: true, AppSidebar: true, StatusBar: true, UpdateBanner: true } },
  })
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

  it('says it is working, and fetches nothing else, until the session resolves', async () => {
    // Regression: none of the three screens rendered while the call was open,
    // which on a slow connection is an empty document — no landmark, no
    // heading, nothing telling a screen reader the page is not broken.
    const load = spyOnLoad()
    deferredFetch()

    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    expect(wrapper.findComponent(SetupForm).exists()).toBe(false)
    expect(wrapper.findComponent(LoginForm).exists()).toBe(false)
    expect(wrapper.find('#main-content').exists()).toBe(false)

    const pending = wrapper.find('[data-testid="session-pending"]')
    expect(pending.text()).not.toBe('')
    expect(wrapper.find('main').attributes('aria-busy')).toBe('true')
    expect(wrapper.find('h1').text()).not.toBe('')

    expect(load.fetchStatus).not.toHaveBeenCalled()
    expect(load.fetchThemes).not.toHaveBeenCalled()
  })

  it('drops the boot screen once an answer picks one of the three', async () => {
    spyOnLoad()
    answerSession(true, false)

    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    expect(wrapper.find('[data-testid="session-pending"]').exists()).toBe(false)
  })

  it('ships that first paint in the document, not only in the bundle', () => {
    // The Vue branch above cannot run until the bundle has downloaded, which on
    // the connection this matters for is the longer half of the wait.
    const html = readFileSync(`${process.cwd()}/index.html`, 'utf8')
    const app = html.match(/<div id="app">([\s\S]*?)<\/div>\s*<script/)

    expect(app, '#app is not the container the bundle mounts into').not.toBeNull()
    expect(app?.[1]).toMatch(/<main\b/)
    expect(app?.[1]).toMatch(/<h1\b/)
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
    // A shell that empties with no word reads as a crash. Said as a notice
    // rather than an error: no field on this form has been touched yet.
    expect(wrapper.findComponent(LoginForm).props('notice')).toBe(SESSION_ENDED)
    expect(wrapper.findComponent(LoginForm).props('error')).toBe('')
  })

  it('lands the session-ended words in a region that was already on screen', async () => {
    // Regression: the watcher set the message before the render, so the form's
    // role="status" node entered the tree already populated — which JAWS reads
    // as page content and never announces (WCAG 4.1.3).
    spyOnLoad()
    answerSession(true, true, AARON)
    const wrapper = mountWithShellStubs()
    await flushPromises()

    useAuthStore().reject()
    await nextTick()
    const region = wrapper.find('#login-status')
    expect(region.exists()).toBe(true)
    expect(region.text()).toBe('')

    await flushPromises()

    expect(wrapper.find('#login-status').element).toBe(region.element)
    expect(wrapper.find('#login-status').text()).toBe(SESSION_ENDED)
  })

  it('marks nothing invalid on the form a sign-out just put up', async () => {
    // Regression: the notice arrived as `error`, and a screen reader announced
    // "Username, invalid entry" on a form nobody had typed into.
    spyOnLoad()
    answerSession(true, true, AARON)
    const wrapper = mountWithShellStubs()
    await flushPromises()

    useAuthStore().reject()
    await flushPromises()

    expect(wrapper.find('#login-status').text()).toBe(SESSION_ENDED)
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()
  })

  it('reports an unreachable server without blaming the empty sign-in form', async () => {
    // Same class as the sign-out notice: nothing has been typed here, so
    // "invalid entry" on both fields is a lie a screen reader reads out.
    spyOnLoad()
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.find('#login-status').text()).toMatch(/did not answer/)
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()
  })

  it('moves to the sign-in form when another tab claimed the instance first', async () => {
    // Regression: the 409 left the setup form up, so "sign in instead" was
    // advice with nowhere to follow it to.
    spyOnLoad()
    answerSession(false, false)
    const wrapper = mount(App)
    await flushPromises()
    expect(wrapper.findComponent(SetupForm).exists()).toBe(true)

    vi.mocked(fetch).mockImplementation((url) =>
      Promise.resolve(
        String(url).endsWith('/setup')
          ? jsonResponse(409, { detail: 'This instance already has an account. Sign in instead.' })
          : jsonResponse(200, { claimed: true, authenticated: false, user: null }),
      ),
    )
    await wrapper.find('#setup-username').setValue('aaron')
    await wrapper.find('#setup-password').setValue('hunter2-hunter2')
    await wrapper.find('#setup-confirmation').setValue('hunter2-hunter2')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.findComponent(SetupForm).exists()).toBe(false)
    const login = wrapper.findComponent(LoginForm)
    expect(login.exists()).toBe(true)
    // Advice about the screen, not a refusal of anything typed into it.
    expect(login.props('notice')).toContain('Sign in instead')
    expect(login.props('error')).toBe('')
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBeUndefined()
  })

  it('keeps the lost race out of the sign-in form it is not about', async () => {
    // Regression: the winning tab shares this cookie jar, so the 409 resolves
    // signed-in and the shell goes up still holding "sign in instead". The
    // next sign-out rendered it as `error` on two untouched fields.
    spyOnLoad()
    answerSession(false, false)
    const wrapper = mountWithShellStubs()
    await flushPromises()

    vi.mocked(fetch).mockImplementation((url) =>
      Promise.resolve(
        String(url).endsWith('/setup')
          ? jsonResponse(409, { detail: 'This instance already has an account. Sign in instead.' })
          : jsonResponse(200, { claimed: true, authenticated: true, user: AARON }),
      ),
    )
    await wrapper.find('#setup-username').setValue('aaron')
    await wrapper.find('#setup-password').setValue('hunter2-hunter2')
    await wrapper.find('#setup-confirmation').setValue('hunter2-hunter2')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('#main-content').exists()).toBe(true)

    vi.mocked(fetch).mockResolvedValue(jsonResponse(204))
    await useAuthStore().signOut()
    await flushPromises()

    expect(wrapper.findComponent(LoginForm).props('error')).toBe('')
    expect(wrapper.find('#login-status').text()).toBe(SESSION_ENDED)
    expect(wrapper.find('#login-username').attributes('aria-invalid')).toBeUndefined()
    expect(wrapper.find('#login-password').attributes('aria-invalid')).toBeUndefined()
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
    expect(wrapper.findComponent(LoginForm).props('notice')).toBe(SESSION_ENDED)
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
