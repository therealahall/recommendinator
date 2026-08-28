import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import router from '@/router'
import AppSidebar from '@/components/organisms/AppSidebar.vue'
import LoginForm from '@/components/organisms/LoginForm.vue'
import SetupForm from '@/components/organisms/SetupForm.vue'
import { ApiError, useApi } from '@/composables/useApi'
import { APP_NAME } from '@/constants/app'
import { PASSWORD_MIN_LENGTH } from '@/constants/auth'
import { useAppStore } from '@/stores/app'
import { SESSION_ENDED, useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { deferredFetch, jsonResponse } from '@/testing/http'
import type { UserResponse } from '@/types/api'

const AARON: UserResponse = {
  id: 1,
  username: 'aaron',
  display_name: 'Aaron Hall',
  password_updated_at: '2026-01-15T09:30:00+00:00',
}

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

function answerSession(
  claimed: boolean,
  authenticated: boolean,
  user: UserResponse | null = null,
  minPasswordLength: number = PASSWORD_MIN_LENGTH,
) {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse(200, { claimed, authenticated, user, min_password_length: minPasswordLength }),
  )
}

function sessionCalls(): number {
  return vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === '/api/auth/session').length
}

const THEMES = [
  { id: 'nord', name: 'Nord', description: '', author: '', version: '1.0.0', theme_type: 'dark' },
  { id: 'snowstorm', name: 'Snowstorm', description: '', author: '', version: '1.0.0', theme_type: 'light' },
]

function answerBoot(storedTheme: string) {
  vi.mocked(fetch).mockImplementation((url) => {
    const path = String(url)
    if (path === '/api/themes') return Promise.resolve(jsonResponse(200, THEMES))
    if (path === '/api/themes/default') return Promise.resolve(jsonResponse(200, { theme: 'nord' }))
    if (path === '/api/users/1/theme') {
      return Promise.resolve(jsonResponse(200, { theme: storedTheme }))
    }
    return Promise.resolve(
      jsonResponse(200, {
        claimed: true,
        authenticated: true,
        user: AARON,
        min_password_length: PASSWORD_MIN_LENGTH,
      }),
    )
  })
}

/** jsdom implements no matchMedia at all, so every mount needs a viewport and a
 *  narrow one has to be played back by hand. */
function stubViewport(narrow: boolean) {
  const listeners = new Set<(change: MediaQueryListEvent) => void>()
  const query = {
    matches: narrow,
    addEventListener: (_: string, listener: (change: MediaQueryListEvent) => void) =>
      listeners.add(listener),
    removeEventListener: (_: string, listener: (change: MediaQueryListEvent) => void) =>
      listeners.delete(listener),
  }
  vi.stubGlobal('matchMedia', () => query)
  return {
    resizeTo(matches: boolean) {
      for (const listener of listeners) listener({ matches } as MediaQueryListEvent)
    },
    watchers: () => listeners.size,
  }
}

async function shell() {
  spyOnLoad()
  answerSession(true, true, AARON)
  const wrapper = mount(App, { shallow: true })
  await flushPromises()
  return wrapper
}

function paintedTheme(): string {
  return (document.getElementById('theme-stylesheet') as HTMLLinkElement | null)?.href ?? ''
}

describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
    stubViewport(false)
  })

  afterEach(() => {
    document.getElementById('theme-stylesheet')?.remove()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  const BOOTS: Array<{ browser: string; cached: string | null; stored: string; painted: string }> = [
    {
      browser: 'the theme picked on another browser, never having picked one here',
      cached: null,
      stored: 'snowstorm',
      painted: 'snowstorm',
    },
    {
      browser: 'the default, on a browser caching a theme this user never picked',
      cached: 'snowstorm',
      stored: '',
      painted: 'nord',
    },
  ]

  // Regression: boot painted the cache, or the config default when there was
  // none, so a pick made elsewhere arrived only on the Preferences page,
  // flipping the theme as it mounted.
  it.each(BOOTS)('boots on $browser', async ({ cached, stored, painted }) => {
    vi.spyOn(useAppStore(), 'fetchStatus').mockResolvedValue(undefined)
    if (cached) localStorage.setItem('theme', cached)
    answerBoot(stored)

    mount(App, { shallow: true })
    await flushPromises()

    expect(paintedTheme()).toContain(`/static/themes/${painted}/colors.css`)
    expect(useThemeStore().currentThemeId).toBe(painted)
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

  it('states the floor the session reported on the setup form it puts up', async () => {
    // Regression: the setup screen stated a floor compiled into the bundle, so
    // a server enforcing another one refused the password it had just invited.
    spyOnLoad()
    const server = PASSWORD_MIN_LENGTH + 4
    answerSession(false, false, null, server)

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.find('#setup-password-hint').text()).toContain(String(server))
  })

  it('puts the sign-in screen back when the session is revoked mid-session', async () => {
    // Regression: a credential revoked while the app was open surfaced as a 401
    // each store swallowed, leaving a half-empty shell and no way to sign in
    // again.
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
    expect(wrapper.findComponent(LoginForm).props('notice')).toBe(SESSION_ENDED)
    expect(wrapper.findComponent(LoginForm).props('error')).toBe('')
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
          : jsonResponse(200, {
              claimed: true,
              authenticated: false,
              user: null,
              min_password_length: PASSWORD_MIN_LENGTH,
            }),
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

  const VIEWPORTS: Array<{ screen: string; narrow: boolean; open: boolean; offscreen: boolean }> = [
    { screen: 'a phone with the sidebar shut', narrow: true, open: false, offscreen: true },
    { screen: 'a phone with the sidebar pulled out', narrow: true, open: true, offscreen: false },
    { screen: 'a desktop with the sidebar pulled out', narrow: false, open: true, offscreen: false },
    // The default state of every desktop load, and the only row that fails if
    // the viewport drops out of the condition and the whole sidebar goes inert.
    { screen: 'a desktop with the toggle never touched', narrow: false, open: false, offscreen: false },
  ]

  // Regression: the shut mobile sidebar was only slid off screen, so Tab still
  // walked through its nav buttons before reaching anything on the page.
  it.each(VIEWPORTS)('hides the sidebar from the keyboard on $screen', async ({ narrow, open, offscreen }) => {
    stubViewport(narrow)
    const wrapper = await shell()

    if (open) await wrapper.find('.sidebar-toggle').trigger('click')

    expect(wrapper.findComponent(AppSidebar).props('offscreen')).toBe(offscreen)
  })

  // `inert` made the toggle a real disclosure control, with no state saying
  // whether pressing it had brought the six nav buttons into being.
  it.each(VIEWPORTS)('states what the toggle controls on $screen', async ({ narrow, open }) => {
    stubViewport(narrow)
    const wrapper = await shell()
    const toggle = wrapper.find('.sidebar-toggle')

    if (open) await toggle.trigger('click')

    const sidebar = mount(AppSidebar, { global: { plugins: [router] } })
    expect(toggle.attributes('aria-controls')).toBe(sidebar.find('aside').attributes('id'))
    expect(toggle.attributes('aria-expanded')).toBe(narrow ? String(open) : undefined)
  })

  // The sign-in, setup and session screens render outside RouterView, so the
  // title named a page nobody could see (WCAG 2.4.2).
  it.each([
    { screen: 'the sign-in form', authenticated: false },
    { screen: 'the shell', authenticated: true },
  ])('titles the document for $screen, not the route behind it', async ({ authenticated }) => {
    spyOnLoad()
    answerSession(true, authenticated, authenticated ? AARON : null)
    await router.push({ name: 'library' })

    mount(App, { shallow: true })
    await flushPromises()

    const page = String(router.currentRoute.value.meta.title)
    expect(document.title.includes(page)).toBe(authenticated)
    expect(document.title).toContain(APP_NAME)
  })

  it('hides it again when the window itself narrows', async () => {
    // The viewport can cross the breakpoint without the toggle being touched —
    // a rotated tablet, a resized window — and the state has to follow it.
    const viewport = stubViewport(false)
    const wrapper = await shell()
    expect(wrapper.findComponent(AppSidebar).props('offscreen')).toBe(false)

    viewport.resizeTo(true)
    await flushPromises()

    expect(wrapper.findComponent(AppSidebar).props('offscreen')).toBe(true)
  })

  it('stops watching the viewport once the shell goes away', async () => {
    const viewport = stubViewport(true)
    const wrapper = await shell()
    expect(viewport.watchers()).toBe(1)

    wrapper.unmount()

    expect(viewport.watchers()).toBe(0)
  })

  it('stops polling the server it can no longer ask when the session ends', async () => {
    // Every /api call 401s from the sign-in screen, and each one signs the
    // session out again, so a poll left running never settles.
    await shell()
    const stopped = vi.spyOn(useAppStore(), 'stopPolling')

    useAuthStore().reject()
    await flushPromises()

    expect(stopped).toHaveBeenCalled()
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
})
