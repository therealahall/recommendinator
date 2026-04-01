import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { useFocusTrap } from './useFocusTrap'

enableAutoUnmount(afterEach)

function createWrapper(template: string, onEscape: () => void) {
  const Comp = defineComponent({
    setup() {
      const containerRef = ref<HTMLElement | null>(null)
      useFocusTrap(containerRef, onEscape)
      return { containerRef }
    },
    template,
  })
  return mount(Comp, { attachTo: document.body })
}

describe('useFocusTrap', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('calls onEscape when Escape is pressed', async () => {
    const onEscape = vi.fn()
    createWrapper(
      '<div ref="containerRef"><button>A</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(onEscape).toHaveBeenCalledOnce()
  })

  it('focuses the container element on mount', async () => {
    const onEscape = vi.fn()
    createWrapper(
      '<div ref="containerRef" id="trap" tabindex="-1"><button>A</button><button>B</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    expect(document.activeElement?.id).toBe('trap')
  })

  it('wraps focus from last to first on Tab', async () => {
    const onEscape = vi.fn()
    const wrapper = createWrapper(
      '<div ref="containerRef"><button id="first">A</button><button id="last">B</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    // Focus the last button
    ;(wrapper.find('#last').element as HTMLElement).focus()
    expect(document.activeElement?.id).toBe('last')

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })
    const prevented = vi.spyOn(event, 'preventDefault')
    document.dispatchEvent(event)

    expect(prevented).toHaveBeenCalled()
    expect(document.activeElement?.id).toBe('first')
  })

  it('wraps focus from first to last on Shift+Tab', async () => {
    const onEscape = vi.fn()
    const wrapper = createWrapper(
      '<div ref="containerRef"><button id="first">A</button><button id="last">B</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    ;(wrapper.find('#first').element as HTMLElement).focus()
    expect(document.activeElement?.id).toBe('first')

    const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })
    const prevented = vi.spyOn(event, 'preventDefault')
    document.dispatchEvent(event)

    expect(prevented).toHaveBeenCalled()
    expect(document.activeElement?.id).toBe('last')
  })

  it('does not throw when container has no focusable elements', async () => {
    const onEscape = vi.fn()
    createWrapper(
      '<div ref="containerRef"><span>No buttons here</span></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    expect(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }))
    }).not.toThrow()
  })

  it('removes event listener on unmount', async () => {
    const onEscape = vi.fn()
    const wrapper = createWrapper(
      '<div ref="containerRef"><button>A</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    wrapper.unmount()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(onEscape).not.toHaveBeenCalled()
  })
})
