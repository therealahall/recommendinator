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

// Not "trakt": the flow belongs to the source being connected, whatever the
// operator named it.
const SOURCE_ID = 'trakt_work'
const SOURCE_NAME = 'Trakt (work)'

// The parent picks the wording, so these tests only follow it through. A
// sentence no branch of that decision produces is what proves the pass-through.
const HINT = 'The remedy the parent worked out.'

function mountFlow(timer: ReturnType<typeof makeTimer>) {
  return mount(TraktDeviceCodeFlow, {
    props: {
      sourceId: SOURCE_ID,
      sourceName: SOURCE_NAME,
      connectHint: HINT,
      setTimer: timer.setTimer,
      clearTimer: timer.clearTimer,
    },
    attachTo: document.body,
  })
}

function setTraktEnabled(enabled: boolean): void {
  useDataStore().oauthStatus[SOURCE_ID] = {
    enabled,
    connected: false,
    authUrl: null,
  }
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
    setTraktEnabled(true)
  })

  it('does not start the device flow while connect is disabled', async () => {
    setTraktEnabled(false)
    const wrapper = mountFlow(makeTimer())

    // aria-disabled leaves the button activatable, so this guard is the only
    // thing between the click and a 400 the user never asked for.
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

    expect(mockPost).toHaveBeenCalledWith('/trakt/start-device-flow', undefined, {
      source_id: SOURCE_ID,
    })
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

  it('transitions from pending to connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'pending', message: 'wait' })
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    // The status re-read that follows a successful poll.
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
    // The confirmation goes to the store, which the panel's live region reads:
    // this component is unmounted by the status flip and cannot announce it.
    expect(useDataStore().oauthMessages[SOURCE_ID]).toBe('Trakt connected!')
    expect(wrapper.text()).not.toContain('Waiting for you to approve')
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
    // Hiding the code panel strands whoever was reading it, so this is the one
    // transition that may claim the keyboard.
    expect(document.activeElement).toBe(
      wrapper.get('[data-testid="trakt-result-panel"]').element,
    )
    wrapper.unmount()
  })

  it.each([
    ['expires', () => mockPost.mockResolvedValueOnce({ connected: false, status: 'expired' })],
    ['is denied', () => mockPost.mockResolvedValueOnce({ connected: false, status: 'denied' })],
    ['cannot be checked', () => mockPost.mockRejectedValueOnce(new Error('offline'))],
  ])('leaves the keyboard alone when the code %s under a user typing elsewhere', async (_name, answerPoll) => {
    mockPost.mockResolvedValueOnce(FLOW)
    answerPoll()
    const timer = makeTimer()
    const wrapper = mountFlow(timer)
    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    // The poll fires from a timer, and the wait is long enough to go and edit
    // another source's fields on the same page.
    const elsewhere = document.createElement('input')
    document.body.appendChild(elsewhere)
    elsewhere.focus()

    await timer.fire()

    expect(wrapper.get('.trakt-flow-status--error').text()).not.toBe('')
    expect(document.activeElement).toBe(elsewhere)
    elsewhere.remove()
    wrapper.unmount()
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
    // The status re-read that follows a successful poll.
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
    expect(useDataStore().oauthMessages[SOURCE_ID]).toBe('Trakt connected!')
    expect(timer.hasPending()).toBe(false)
  })

  it('says so when the connect succeeded but the status re-read did not', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    mockGet.mockRejectedValue(new Error('status read failed'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    await timer.fire()

    // The failed re-read leaves `connected` false, so the parent keeps this
    // component mounted: staying silent rendered an empty box with no controls.
    expect(useDataStore().oauthStatusFor(SOURCE_ID).connected).toBe(false)
    const result = wrapper.get('[data-testid="trakt-result-panel"]')
    expect(result.get('[data-testid="trakt-result-text"]').text()).toContain(
      'could not be re-read',
    )
    wrapper.unmount()
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
