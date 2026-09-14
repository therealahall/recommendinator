import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DeviceCodeFlow from './DeviceCodeFlow.vue'
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

const SOURCE_ID = 'fake_work'
const SOURCE_NAME = 'Fake (work)'

const HINT = 'The remedy the parent worked out.'

function mountFlow(timer: ReturnType<typeof makeTimer>) {
  return mount(DeviceCodeFlow, {
    props: {
      sourceId: SOURCE_ID,
      sourceName: SOURCE_NAME,
      plugin: 'fake',
      serviceName: 'Fake',
      connectHint: HINT,
      setTimer: timer.setTimer,
      clearTimer: timer.clearTimer,
    },
    attachTo: document.body,
  })
}

function setConnectEnabled(enabled: boolean): void {
  useDataStore().oauthStatus[SOURCE_ID] = {
    enabled,
    connected: false,
    authUrl: null,
  }
}

function codePanelVisible(
  wrapper: ReturnType<typeof mountFlow>,
): boolean {
  const code = wrapper.find('[data-testid="device-user-code"]')
  if (!code.exists()) return false
  const panel = code.element.closest('.device-flow-panel') as HTMLElement
  return panel.style.display !== 'none'
}

const FLOW = {
  user_code: 'ABCD-1234',
  verification_url: 'https://fake.example.com/activate',
  device_code: 'dev-code',
  expires_in: 600,
  interval: 5,
}

describe('DeviceCodeFlow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockDelete.mockReset()
    setConnectEnabled(true)
  })

  it('does not start the device flow while connect is disabled', async () => {
    setConnectEnabled(false)
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()

    expect(mockPost).not.toHaveBeenCalled()
  })

  it('shows the user code and verification link after starting', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()

    expect(mockPost).toHaveBeenCalledWith('/oauth/fake/start-device-flow', undefined, {
      source_id: SOURCE_ID,
    })
    expect(wrapper.get('[data-testid="device-user-code"]').text()).toContain(
      'ABCD-1234',
    )
    const link = wrapper.get('[data-testid="device-verification-link"]')
    expect(link.attributes('href')).toBe('https://fake.example.com/activate')
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toBe('noopener noreferrer')
    expect(link.text()).toContain('opens in new tab')
    expect(timer.setTimer).toHaveBeenCalledTimes(1)
  })

  it('transitions from pending to connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'pending', message: 'wait' })
      .mockResolvedValueOnce({ connected: true, message: 'Fake connected!' })
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()

    await timer.fire()
    expect(codePanelVisible(wrapper)).toBe(true)

    await timer.fire()
    expect(mockPost).toHaveBeenLastCalledWith(
      '/oauth/fake/poll-device-approval',
      { device_code: 'dev-code' },
      { source_id: SOURCE_ID },
    )
    expect(useDataStore().oauthMessages[SOURCE_ID]).toBe('Fake connected!')
    expect(wrapper.text()).not.toContain('Waiting for you to approve')
    expect(codePanelVisible(wrapper)).toBe(false)
    expect(timer.hasPending()).toBe(false)
  })

  it('shows an error with retry on expired', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'expired', message: 'expired' })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()
    await timer.fire()

    expect(wrapper.get('.device-flow-status--error').text()).toContain('expired')
    expect(wrapper.find('[data-testid="device-retry-btn"]').exists()).toBe(true)
    expect(timer.hasPending()).toBe(false)
    expect(document.activeElement).toBe(
      wrapper.get('[data-testid="device-result-panel"]').element,
    )
    wrapper.unmount()
  })

  it.each([
    ['expires', () => mockPost.mockResolvedValueOnce({ connected: false, status: 'expired' })],
    ['cannot be checked', () => mockPost.mockRejectedValueOnce(new Error('offline'))],
  ])('leaves the keyboard alone when the code %s under a user typing elsewhere', async (_name, answerPoll) => {
    mockPost.mockResolvedValueOnce(FLOW)
    answerPoll()
    const timer = makeTimer()
    const wrapper = mountFlow(timer)
    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()
    const elsewhere = document.createElement('input')
    document.body.appendChild(elsewhere)
    elsewhere.focus()

    await timer.fire()

    expect(wrapper.get('.device-flow-status--error').text()).not.toBe('')
    expect(document.activeElement).toBe(elsewhere)
    elsewhere.remove()
    wrapper.unmount()
  })

  it('shows an error when the device flow cannot start', async () => {
    mockPost.mockRejectedValueOnce(new Error('bad creds'))
    const wrapper = mountFlow(makeTimer())

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.device-flow-status--error').text()).toContain(
      'Could not start the Fake connection',
    )
    expect(wrapper.find('[data-testid="device-retry-btn"]').exists()).toBe(true)
  })

  it('backs off by +5s on slow_down and keeps polling until connected', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: false, status: 'slow_down', message: 'slow' })
      .mockResolvedValueOnce({ connected: true, message: 'Fake connected!' })
    mockGet.mockResolvedValue({ enabled: true, connected: true })
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()
    expect(timer.setTimer).toHaveBeenLastCalledWith(expect.any(Function), 5000)

    await timer.fire()
    expect(wrapper.text()).toContain('slow down')
    expect(timer.setTimer).toHaveBeenLastCalledWith(expect.any(Function), 10000)
    expect(timer.hasPending()).toBe(true)

    await timer.fire()
    expect(useDataStore().oauthMessages[SOURCE_ID]).toBe('Fake connected!')
    expect(timer.hasPending()).toBe(false)
  })

  it('says so when the connect succeeded but the status re-read did not', async () => {
    mockPost
      .mockResolvedValueOnce(FLOW)
      .mockResolvedValueOnce({ connected: true, message: 'Fake connected!' })
    mockGet.mockRejectedValue(new Error('status read failed'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()

    await timer.fire()

    expect(useDataStore().oauthStatusFor(SOURCE_ID).connected).toBe(false)
    const result = wrapper.get('[data-testid="device-result-panel"]')
    expect(result.get('[data-testid="device-result-text"]').text()).toContain(
      'could not be re-read',
    )
    wrapper.unmount()
  })

  it('stops polling on unmount', async () => {
    mockPost.mockResolvedValueOnce(FLOW)
    const timer = makeTimer()
    const wrapper = mountFlow(timer)

    await wrapper.get('[data-testid="device-connect-btn"]').trigger('click')
    await flushPromises()
    expect(timer.hasPending()).toBe(true)

    wrapper.unmount()
    expect(timer.clearTimer).toHaveBeenCalled()
    expect(timer.hasPending()).toBe(false)
  })
})
