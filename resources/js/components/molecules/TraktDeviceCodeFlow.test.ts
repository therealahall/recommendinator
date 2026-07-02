import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import TraktDeviceCodeFlow from './TraktDeviceCodeFlow.vue'
import { useDataStore } from '@/stores/data'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/composables/useApi', () => ({
  ApiError: class ApiError extends Error {
    constructor(public status: number, public statusText: string) {
      super(`${status} ${statusText}`)
      this.name = 'ApiError'
    }
  },
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn(),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
    raw: vi.fn(),
  }),
}))

// A controllable timer: schedulePoll hands us the callback and we fire it on
// demand, so the poll loop advances without waiting real seconds.
function makeTimer() {
  let pending: (() => void) | null = null
  const setTimer = vi.fn((handler: () => void) => {
    pending = handler
    return 1
  })
  const clearTimer = vi.fn(() => {
    pending = null
  })
  async function fire() {
    const handler = pending
    pending = null
    handler?.()
    await flushPromises()
  }
  return { setTimer, clearTimer, fire, hasPending: () => pending !== null }
}

function mountFlow(timer: ReturnType<typeof makeTimer>) {
  return mount(TraktDeviceCodeFlow, {
    props: { setTimer: timer.setTimer, clearTimer: timer.clearTimer },
    attachTo: document.body,
  })
}

// The code panel stays mounted across states (v-show), so visibility — not
// existence — distinguishes the awaiting state from the connected/error one.
function codePanelVisible(
  wrapper: ReturnType<typeof mountFlow>,
): boolean {
  const code = wrapper.find('[data-testid="trakt-user-code"]')
  if (!code.exists()) return false
  const panel = code.element.closest('.trakt-flow-panel') as HTMLElement
  return panel.style.display !== 'none'
}

const FLOW = {
  user_code: 'ABCD-1234',
  verification_url: 'https://trakt.tv/activate',
  device_code: 'dev-code',
  expires_in: 600,
  interval: 5,
}

describe('TraktDeviceCodeFlow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockDelete.mockReset()
    // Client credentials resolve by default so the connect action is live;
    // the gating tests below flip this off explicitly.
    useDataStore().traktStatus.enabled = true
  })

  it('renders the connect trigger initially', () => {
    const wrapper = mountFlow(makeTimer())
    expect(wrapper.get('[data-testid="trakt-connect-btn"]').text()).toBe(
      'Connect Trakt Account',
    )
  })

  it('enables the connect button and omits the hint when credentials resolve', () => {
    const wrapper = mountFlow(makeTimer())
    const button = wrapper.get('[data-testid="trakt-connect-btn"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(false)
    expect(button.attributes('aria-describedby')).toBeUndefined()
    expect(wrapper.find('[data-testid="trakt-connect-hint"]').exists()).toBe(
      false,
    )
  })

  it('disables connect and shows an accessible hint when credentials are missing', () => {
    useDataStore().traktStatus.enabled = false
    const wrapper = mountFlow(makeTimer())

    const button = wrapper.get('[data-testid="trakt-connect-btn"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    const hint = wrapper.get('[data-testid="trakt-connect-hint"]')
    expect(hint.text()).toContain('client ID')
    expect(hint.text()).toContain('client secret')

    // The hint is programmatically associated with the disabled button so a
    // screen reader announces "why" alongside the control.
    expect(button.attributes('aria-describedby')).toBe('trakt-connect-hint')
    expect(hint.attributes('id')).toBe('trakt-connect-hint')
  })

  it('does not start the device flow while connect is disabled', async () => {
    useDataStore().traktStatus.enabled = false
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    expect(mockPost).not.toHaveBeenCalled()
  })

  it('shows the user code and verification link after starting', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    expect(mockPost).toHaveBeenCalledWith('/trakt/start-device-flow')
    expect(wrapper.get('[data-testid="trakt-user-code"]').text()).toContain(
      'ABCD-1234',
    )
    const link = wrapper.get('[data-testid="trakt-verification-link"]')
    expect(link.attributes('href')).toBe('https://trakt.tv/activate')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    // The new-tab behaviour is announced to screen readers, not implied.
    expect(link.text()).toContain('opens in new tab')
    // Polling was scheduled, not invoked synchronously.
    expect(timer.setTimer).toHaveBeenCalledTimes(1)
  })

  it('exposes an aria-live status region while awaiting approval', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    const live = wrapper.get('[role="status"][aria-live="polite"]')
    expect(live.text()).toContain('Waiting for you to approve')
  })

  it('conveys the activation code to screen readers, not by styling alone', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    expect(
      wrapper.get('[data-testid="trakt-user-code"] .sr-only').text(),
    ).toContain('activation code')
  })

  it('transitions from pending to connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'pending', message: 'wait' })
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    // loadSyncSources after success: config/reload POST + four GETs.
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    await timer.fire() // first poll -> pending, reschedules
    // The code panel is shown while awaiting (v-show keeps it mounted so the
    // shared live region is never re-created).
    expect(codePanelVisible(wrapper)).toBe(true)

    await timer.fire() // second poll -> connected
    expect(wrapper.text()).toContain('Trakt connected!')
    // Once connected the code panel is hidden, not unmounted.
    expect(codePanelVisible(wrapper)).toBe(false)
    // No further poll scheduled once connected.
    expect(timer.hasPending()).toBe(false)
  })

  it('shows an error with retry on expired', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'expired', message: 'expired' })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    await timer.fire()

    expect(wrapper.get('.trakt-flow-status--error').text()).toContain('expired')
    expect(wrapper.find('[data-testid="trakt-retry-btn"]').exists()).toBe(true)
    expect(timer.hasPending()).toBe(false)
  })

  it('shows an error with retry on denied', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'denied', message: 'denied' })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    await timer.fire()

    expect(wrapper.get('.trakt-flow-status--error').text()).toContain('denied')
    expect(wrapper.find('[data-testid="trakt-retry-btn"]').exists()).toBe(true)
  })

  it('shows an error when the device flow cannot start', async () => {
    mockPost.mockRejectedValueOnce(new Error('bad creds'))
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.trakt-flow-status--error').text()).toContain(
      'Could not start the Trakt connection',
    )
    expect(wrapper.find('[data-testid="trakt-retry-btn"]').exists()).toBe(true)
  })

  it('backs off by +5s on slow_down and keeps polling until connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'slow_down', message: 'slow' })
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    // loadSyncSources after success: config/reload POST + four GETs.
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    // Initial schedule uses the server interval (5s).
    expect(timer.setTimer).toHaveBeenLastCalledWith(expect.any(Function), 5000)

    await timer.fire() // poll -> slow_down: interval grows to 10s, keep polling
    expect(wrapper.text()).toContain('slow down')
    expect(timer.setTimer).toHaveBeenLastCalledWith(expect.any(Function), 10000)
    expect(timer.hasPending()).toBe(true)

    await timer.fire() // next poll -> connected
    expect(wrapper.text()).toContain('Trakt connected!')
    expect(timer.hasPending()).toBe(false)
  })

  it('keeps a single persistent live region across starting → awaiting → connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    mockPost.mockResolvedValue({})
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    const awaitingRegion = wrapper.get('.trakt-flow-status').element

    await timer.fire() // -> connected
    expect(wrapper.text()).toContain('Trakt connected!')
    // The same DOM node carried the message through every state change
    // rather than being torn down and re-created (which JAWS would skip).
    expect(wrapper.get('.trakt-flow-status').element).toBe(awaitingRegion)
    expect(awaitingRegion.getAttribute('aria-atomic')).toBe('true')
  })

  it('applies the error styling class to the persistent region on failure', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'expired', message: 'expired' })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    const region = wrapper.get('.trakt-flow-status').element

    await timer.fire() // -> expired (error)
    // Same node, now carrying the error styling — colour is not the sole signal.
    expect(wrapper.get('.trakt-flow-status').element).toBe(region)
    expect(region.classList.contains('trakt-flow-status--error')).toBe(true)
  })

  it('stops polling on unmount', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    expect(timer.hasPending()).toBe(true)

    wrapper.unmount()
    expect(timer.clearTimer).toHaveBeenCalled()
    expect(timer.hasPending()).toBe(false)
  })
})
