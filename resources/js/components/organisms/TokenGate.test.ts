import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import TokenGate from './TokenGate.vue'
import { useAuthStore } from '@/stores/auth'
import { deferredFetch, jsonResponse } from '@/testing/http'

async function enterToken(wrapper: VueWrapper, value: string): Promise<void> {
  await wrapper.find('#token-gate-input').setValue(value)
  await wrapper.find('form').trigger('submit')
}

describe('TokenGate', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('unlocks the app only once the server has accepted the token', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200))
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'the-token')
    await flushPromises()

    expect(useAuthStore().isAuthenticated).toBe(true)
    expect(localStorage.getItem('apiToken')).toBe('the-token')
  })

  it('focuses the field on mount, the only thing this screen offers', () => {
    const wrapper = mount(TokenGate, { attachTo: document.body })

    expect(document.activeElement).toBe(wrapper.find('#token-gate-input').element)
  })

  it('will not submit an empty token', async () => {
    const wrapper = mount(TokenGate)

    await wrapper.find('form').trigger('submit')

    expect(useAuthStore().isAuthenticated).toBe(false)
    expect(fetch).not.toHaveBeenCalled()
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('masks the field, since a token is a credential', () => {
    const wrapper = mount(TokenGate)

    expect(wrapper.find('#token-gate-input').attributes('type')).toBe('password')
  })

  it('mounts the live region before it has anything to say', () => {
    const wrapper = mount(TokenGate)

    const region = wrapper.find('#token-gate-status')
    expect(region.exists()).toBe(true)
    expect(region.attributes('role')).toBe('status')
    expect(region.text()).toBe('')
  })

  it('transitions one region through the whole attempt without remounting it', async () => {
    // Regression: unlocking on submit destroyed the gate, the shell's requests
    // 401'd, and a new gate mounted with the refusal already in its region. A
    // pre-populated region reads as page content and is skipped (WCAG 4.1.3).
    const answer = deferredFetch()
    const wrapper = mount(TokenGate)
    const region = wrapper.find('#token-gate-status').element

    await enterToken(wrapper, 'wrong-token')
    expect(wrapper.find('#token-gate-status').text()).toBe('Checking token…')
    expect(useAuthStore().isAuthenticated).toBe(false)

    answer(jsonResponse(401))
    await flushPromises()

    expect(wrapper.find('#token-gate-status').element).toBe(region)
    expect(wrapper.find('#token-gate-status').text()).toContain('not accepted')
    expect(wrapper.find('#token-gate-input').attributes('aria-invalid')).toBe('true')
  })

  it('locks the submit button without blurring it while checking', async () => {
    deferredFetch()
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'the-token')

    const button = wrapper.find('button[type="submit"]')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('blames the server, not the token, when the request never lands', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'untested-token')
    await flushPromises()

    expect(wrapper.find('#token-gate-status').text()).toContain('did not confirm the token')
    expect(wrapper.find('#token-gate-input').attributes('aria-invalid')).toBeUndefined()
  })

  it('does not report an unreachable server when the server answered', async () => {
    // A 500, 503 or 429 reached the server. Wording it as a failure to reach
    // sends the user to check something that is not wrong, and a screen-reader
    // user has no network tab to correct the story from.
    vi.mocked(fetch).mockResolvedValue(jsonResponse(503))
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'untested-token')
    await flushPromises()

    const status = wrapper.find('#token-gate-status')
    expect(status.text()).toContain('did not confirm the token')
    expect(status.text()).not.toContain('not accepted')
    expect(wrapper.find('#token-gate-input').attributes('aria-invalid')).toBeUndefined()
  })

  it('keeps the typed token after a refusal, so it need not be re-pasted', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401))
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'nearly-right-token')
    await flushPromises()

    expect(wrapper.find<HTMLInputElement>('#token-gate-input').element.value).toBe(
      'nearly-right-token',
    )
  })

  it('clears the field only once the token has been accepted', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(200))
    const wrapper = mount(TokenGate)

    await enterToken(wrapper, 'the-token')
    await flushPromises()

    expect(wrapper.find<HTMLInputElement>('#token-gate-input').element.value).toBe('')
  })

  it('describes the field by the region only while it has something to say', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(401))
    const wrapper = mount(TokenGate)
    expect(wrapper.find('#token-gate-input').attributes('aria-describedby')).toBeUndefined()

    await enterToken(wrapper, 'wrong-token')
    await flushPromises()

    expect(wrapper.find('#token-gate-input').attributes('aria-describedby')).toBe(
      'token-gate-status',
    )
  })
})
