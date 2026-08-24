import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import StatusBar from './StatusBar.vue'
import { useAppStore } from '@/stores/app'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

function mainRegion(): HTMLElement {
  const main = document.createElement('main')
  main.id = 'main-content'
  main.tabIndex = -1
  document.body.appendChild(main)
  return main
}

describe('StatusBar', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('is hidden when statusMessage is empty', () => {
    const wrapper = mount(StatusBar)
    expect(wrapper.find('.status-bar').isVisible()).toBe(false)
  })

  it('is visible with correct content and aria-atomic when statusMessage is set', () => {
    const app = useAppStore()
    app.statusMessage = 'System initializing...'

    const wrapper = mount(StatusBar)
    const bar = wrapper.find('.status-bar')
    expect(bar.isVisible()).toBe(true)
    expect(bar.text()).toBe('System initializing...')
    expect(bar.attributes('aria-atomic')).toBe('true')
  })

  it('applies error role and assertive aria-live when status is error', () => {
    const app = useAppStore()
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'

    const wrapper = mount(StatusBar)
    const bar = wrapper.find('.status-bar')
    expect(bar.attributes('role')).toBe('alert')
    expect(bar.attributes('aria-live')).toBe('assertive')
    expect(bar.classes()).toContain('error')
    expect(bar.classes()).not.toContain('loading')
  })

  it('applies status role and polite aria-live when loading', () => {
    const app = useAppStore()
    app.status = 'loading'
    app.statusMessage = 'System initializing...'

    const wrapper = mount(StatusBar)
    const bar = wrapper.find('.status-bar')
    expect(bar.attributes('role')).toBe('status')
    expect(bar.attributes('aria-live')).toBe('polite')
    expect(bar.classes()).toContain('loading')
    expect(bar.classes()).not.toContain('error')
  })

  it('is hidden when ready with no modifier classes applied', () => {
    const app = useAppStore()
    app.status = 'ready'

    const wrapper = mount(StatusBar)
    const bar = wrapper.find('.status-bar')
    expect(bar.isVisible()).toBe(false)
    expect(bar.classes()).not.toContain('success')
    expect(bar.classes()).not.toContain('error')
    expect(bar.classes()).not.toContain('loading')
  })

  it('offers a way to ask the server again once it could not be reached', async () => {
    // Regression: the connection error was final — no control, and nothing
    // saying a reload was the only way out of it.
    const app = useAppStore()
    const retry = vi.spyOn(app, 'fetchStatus').mockResolvedValue(undefined)
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'

    const wrapper = mount(StatusBar)
    await wrapper.find('[data-testid="status-retry"]').trigger('click')

    expect(retry).toHaveBeenCalled()
  })

  it('says something new while the retry runs, so a repeat failure is announced', async () => {
    const app = useAppStore()
    let settle: () => void = () => {}
    vi.spyOn(app, 'fetchStatus').mockImplementation(
      () => new Promise((resolve) => { settle = () => resolve() }),
    )
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'
    const wrapper = mount(StatusBar)
    const failure = wrapper.find('.status-bar').text()

    await wrapper.find('[data-testid="status-retry"]').trigger('click')

    expect(wrapper.find('.status-bar').text()).not.toBe(failure)

    settle()
    await flushPromises()

    expect(wrapper.find('.status-bar').text()).toBe(failure)
  })

  it('keeps Try again focusable while the retry is in flight', async () => {
    const app = useAppStore()
    vi.spyOn(app, 'fetchStatus').mockImplementation(() => new Promise(() => {}))
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'
    const wrapper = mount(StatusBar, { attachTo: document.body })
    const button = wrapper.find('[data-testid="status-retry"]')
    ;(button.element as HTMLButtonElement).focus()

    await button.trigger('click')

    expect(button.attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(button.element)
    wrapper.unmount()
  })

  it('lands focus on the main region when a working retry takes Try again away', async () => {
    const app = useAppStore()
    const main = mainRegion()
    vi.spyOn(app, 'fetchStatus').mockImplementation(async () => {
      app.status = 'ready'
      app.statusMessage = ''
    })
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'
    const wrapper = mount(StatusBar, { attachTo: main })
    const button = wrapper.find('[data-testid="status-retry"]')
    ;(button.element as HTMLButtonElement).focus()

    await button.trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="status-retry"]').exists()).toBe(false)
    expect(document.activeElement).toBe(main)
    wrapper.unmount()
    main.remove()
  })

  it('leaves focus alone when the retry fails and Try again is still there', async () => {
    const app = useAppStore()
    const main = mainRegion()
    vi.spyOn(app, 'fetchStatus').mockResolvedValue(undefined)
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'
    const wrapper = mount(StatusBar, { attachTo: document.body })
    const button = wrapper.find('[data-testid="status-retry"]')
    ;(button.element as HTMLButtonElement).focus()

    await button.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(button.element)
    wrapper.unmount()
    main.remove()
  })

  it('does not ask the server twice while the first attempt is in flight', async () => {
    const app = useAppStore()
    const retry = vi.spyOn(app, 'fetchStatus').mockImplementation(() => new Promise(() => {}))
    app.status = 'error'
    app.statusMessage = 'Failed to connect to server'
    const wrapper = mount(StatusBar)
    const button = wrapper.find('[data-testid="status-retry"]')

    await button.trigger('click')
    await button.trigger('click')

    expect(retry).toHaveBeenCalledTimes(1)
  })
})
