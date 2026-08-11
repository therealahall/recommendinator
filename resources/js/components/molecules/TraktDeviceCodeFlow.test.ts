import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import TraktDeviceCodeFlow from './TraktDeviceCodeFlow.vue'
import { useDataStore } from '@/stores/data'
import { componentStyles } from '@/testing/styles'

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

// Not "trakt": the flow belongs to the source being connected, whatever the
// operator named it.
const SOURCE_ID = 'trakt_work'
const SOURCE_NAME = 'Trakt (work)'

function mountFlow(timer: ReturnType<typeof makeTimer>) {
  return mount(TraktDeviceCodeFlow, {
    props: {
      sourceId: SOURCE_ID,
      sourceName: SOURCE_NAME,
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
    setTraktEnabled(false)
    const wrapper = mountFlow(makeTimer())

    const button = wrapper.get('[data-testid="trakt-connect-btn"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    const hint = wrapper.get('[data-testid="trakt-connect-hint"]')
    expect(hint.text()).toContain('client ID')
    expect(hint.text()).toContain('client secret')

    // The hint is programmatically associated with the disabled button so a
    // screen reader announces "why" alongside the control.
    expect(hint.attributes('id')).toBe('trakt-connect-hint-trakt_work')
    expect(button.attributes('aria-describedby')).toBe(hint.attributes('id'))
  })

  it('gives each source its own hint id', () => {
    setTraktEnabled(false)
    const first = mountFlow(makeTimer())
    const second = mount(TraktDeviceCodeFlow, {
      props: {
        sourceId: 'trakt_home',
        sourceName: 'Trakt (home)',
        setTimer: vi.fn(),
        clearTimer: vi.fn(),
      },
      attachTo: document.body,
    })

    // Two Trakt sources sit in one document; a shared id would point both
    // buttons at the first panel's hint.
    const hintId = (wrapper: typeof first) =>
      wrapper.get('[data-testid="trakt-connect-hint"]').attributes('id')
    expect(hintId(first)).not.toBe(hintId(second))
    expect(
      second.get('[data-testid="trakt-connect-btn"]').attributes('aria-describedby'),
    ).toBe(hintId(second))
    first.unmount()
    second.unmount()
  })

  it('names the connect button for its source, not just for Trakt', () => {
    const first = mountFlow(makeTimer())
    const second = mount(TraktDeviceCodeFlow, {
      props: {
        sourceId: 'trakt_home',
        sourceName: 'Trakt (home)',
        setTimer: vi.fn(),
        clearTimer: vi.fn(),
      },
      attachTo: document.body,
    })

    // Two expanded Trakt panels put two "Connect Trakt Account" entries in an
    // element list that shows names and nothing else.
    const button = (wrapper: typeof first) =>
      wrapper.get('[data-testid="trakt-connect-btn"]')
    expect(button(first).attributes('aria-label')).toBe(
      'Connect Trakt Account for Trakt (work)',
    )
    expect(button(second).attributes('aria-label')).toBe(
      'Connect Trakt Account for Trakt (home)',
    )
    // The visible words stay inside the accessible name (WCAG 2.5.3).
    expect(button(first).attributes('aria-label')).toContain(
      button(first).text(),
    )
    first.unmount()
    second.unmount()
  })

  it('does not start the device flow while connect is disabled', async () => {
    setTraktEnabled(false)
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
    // The parent unmounts this component on a clean re-read, so grabbing focus
    // here would only fight the parent's own move.
    expect(document.activeElement).not.toBe(
      wrapper.get('[data-testid="trakt-result-panel"]').element,
    )
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

  it('has the live region mounted and visible before the flow starts', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const wrapper = mountFlow(makeTimer())

    // v-show hid it until the first message existed, and display:none is out
    // of the accessibility tree — so "Requesting a device code…" arrived with
    // the region rather than as a change to it, and was never announced.
    const region = wrapper.get('.trakt-flow-status').element as HTMLElement
    expect(region.textContent).toBe('')
    expect(region.style.display).not.toBe('none')

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.trakt-flow-status').element).toBe(region)
    expect(region.textContent).toContain('Waiting for you to approve')
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
    // Where the flow leaves a keyboard user: the code panel v-show is about to
    // hide, taking their focus with it in a browser.
    const link = wrapper.get('[data-testid="trakt-verification-link"]')
      .element as HTMLElement
    link.focus()

    await timer.fire()

    // The failed re-read leaves `connected` false, so the parent keeps this
    // component mounted: staying silent rendered an empty box with no controls.
    expect(useDataStore().oauthStatusFor(SOURCE_ID).connected).toBe(false)
    const result = wrapper.get('[data-testid="trakt-result-panel"]')
    expect(result.get('[data-testid="trakt-result-text"]').text()).toContain(
      'could not be re-read',
    )
    // One region speaks: the store's confirmation. A second polite region
    // going non-empty in the same tick queues two announcements.
    expect(useDataStore().oauthMessages[SOURCE_ID]).toBe('Trakt connected!')
    expect(wrapper.get('.trakt-flow-status').element.textContent).toBe('')

    expect(document.activeElement).toBe(result.element)
    expect(result.attributes('role')).toBe('group')
    expect(result.attributes('aria-label')).toBe('Trakt (work) connection result')
    wrapper.unmount()
  })

  it('leaves focus alone when the poll lands somewhere the user is typing', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    mockGet.mockRejectedValue(new Error('status read failed'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    // The settings form sits below this component. poll() fires from a timer,
    // so a user who tabbed down there while waiting for approval is holding
    // focus in a field no state change removes (WCAG 2.4.3).
    const field = document.createElement('input')
    document.body.appendChild(field)
    field.focus()

    await timer.fire()

    expect(document.activeElement).toBe(field)
    field.remove()
    wrapper.unmount()
  })

  it('suppresses the result panel focus ring for pointer focus alone', () => {
    // Programmatic focus matches :focus-visible when the element that lost
    // focus did, so a blanket `:focus { outline: none }` erased the ring for
    // the keyboard user it exists for (2.4.7). The scoped rule outranks
    // base.css, which cannot rescue it.
    const styles = componentStyles(
      'resources/js/components/molecules/TraktDeviceCodeFlow.vue',
    )

    expect(styles).toMatch(
      /\.trakt-flow-panel:focus:not\(:focus-visible\)\s*\{[^}]*outline:\s*none/,
    )
    expect(styles).not.toMatch(/\.trakt-flow-panel:focus\s*\{/)
  })

  it('keeps a single persistent live region across starting → awaiting → connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: true, message: 'Trakt connected!' })
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()
    const awaitingRegion = wrapper.get('.trakt-flow-status').element
    expect(awaitingRegion.textContent).toContain('Waiting for you to approve')

    await timer.fire() // -> connected
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

  it('names its source in the live region, which a timer drives', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const wrapper = mountFlow(makeTimer())
    const region = wrapper.get('.trakt-flow-status').element
    // Silent before the flow starts, so the prefix cannot arrive on its own.
    expect(region.textContent).toBe('')

    await wrapper.get('[data-testid="trakt-connect-btn"]').trigger('click')
    await flushPromises()

    // The poll announces on a background timer, so a user standing in another
    // expanded panel hears this. aria-atomic reads the prefix with it.
    expect(wrapper.get('.trakt-flow-status').element).toBe(region)
    expect(wrapper.get('.trakt-flow-status .sr-only').text()).toBe(
      'Trakt (work):',
    )
    expect(region.textContent).toContain(
      'Trakt (work): Waiting for you to approve',
    )
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
