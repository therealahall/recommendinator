import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import TokenGate from '@/components/organisms/TokenGate.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { jsonResponse } from '@/testing/http'

/** Neutralise the four calls App fires on unlock so they can be counted rather
 *  than performed. Returns the spies plus the one call the gate itself makes. */
function spyOnLoad() {
  const app = useAppStore()
  const theme = useThemeStore()
  return {
    fetchStatus: vi.spyOn(app, 'fetchStatus').mockResolvedValue(undefined),
    fetchUsers: vi.spyOn(app, 'fetchUsers').mockResolvedValue(undefined),
    fetchThemes: vi.spyOn(theme, 'fetchThemes').mockResolvedValue(undefined),
    applyStoredTheme: vi.spyOn(theme, 'applyStoredTheme').mockImplementation(() => {}),
  }
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

  it('shows the gate and fetches nothing while there is no token', async () => {
    const load = spyOnLoad()

    const wrapper = mount(App, { shallow: true })
    await flushPromises()

    expect(wrapper.findComponent(TokenGate).exists()).toBe(true)
    expect(wrapper.find('#main-content').exists()).toBe(false)
    expect(load.fetchStatus).not.toHaveBeenCalled()
    expect(load.fetchUsers).not.toHaveBeenCalled()
    expect(load.fetchThemes).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('applies the stored theme while the gate is still up', async () => {
    // Themes are static files behind no token, and someone who picked the light
    // theme because they cannot read light-on-dark cannot skip the gate.
    const load = spyOnLoad()

    mount(App, { shallow: true })
    await flushPromises()

    expect(load.applyStoredTheme).toHaveBeenCalledTimes(1)
  })

  it('loads the app exactly once when the first token is accepted', async () => {
    const load = spyOnLoad()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { status: 'ready' }))
    const wrapper = mount(App, { shallow: true })

    await useAuthStore().submitToken('the-token')
    await flushPromises()

    expect(wrapper.findComponent(TokenGate).exists()).toBe(false)
    expect(load.fetchStatus).toHaveBeenCalledTimes(1)
    expect(load.fetchUsers).toHaveBeenCalledTimes(1)
    expect(load.fetchThemes).toHaveBeenCalledTimes(1)
  })

  it('loads on mount when a verified token is already in storage', async () => {
    localStorage.setItem('apiToken', 'stored-token')
    const load = spyOnLoad()

    mount(App, { shallow: true })
    await flushPromises()

    expect(load.fetchStatus).toHaveBeenCalledTimes(1)
  })

  it('puts the gate back when the token is revoked mid-session', async () => {
    // Regression: a token revoked while the app was open surfaced as a 401 each
    // store swallowed, leaving a half-empty shell and no way to enter a new
    // token. reject() now has to take the whole shell down with it.
    localStorage.setItem('apiToken', 'revoked-token')
    spyOnLoad()
    const wrapper = mount(App, { shallow: true })
    await flushPromises()
    expect(wrapper.findComponent(TokenGate).exists()).toBe(false)

    useAuthStore().reject()
    await flushPromises()

    expect(wrapper.findComponent(TokenGate).exists()).toBe(true)
    expect(wrapper.find('#main-content').exists()).toBe(false)
  })

  it('moves focus to the main landmark when the shell replaces the gate', async () => {
    // The gate takes the focused input with it, so focus falls back to <body>
    // and the next Tab restarts from the sidebar.
    spyOnLoad()
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200, { status: 'ready' }))
    const wrapper = mount(App, { shallow: true, attachTo: document.body })

    await useAuthStore().submitToken('the-token')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.find('#main-content').element)
    wrapper.unmount()
  })
})
