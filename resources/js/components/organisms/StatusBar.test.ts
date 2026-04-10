import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import StatusBar from './StatusBar.vue'
import { useAppStore } from '@/stores/app'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

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

  it('applies status role and polite aria-live when ready', () => {
    const app = useAppStore()
    app.status = 'ready'
    // Force visibility to verify ARIA attributes in ready state
    app.statusMessage = 'Ready'

    const wrapper = mount(StatusBar)
    const bar = wrapper.find('.status-bar')
    expect(bar.attributes('role')).toBe('status')
    expect(bar.attributes('aria-live')).toBe('polite')
  })
})
