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

  it('keeps Tab inside the container when it has no focusable elements', async () => {
    // Regression: the trap used to return without preventDefault when the
    // query found nothing, so Tab escaped to the first focusable in the
    // document — a control behind an aria-modal dialog, invisible to the
    // screen reader's virtual cursor (WCAG 2.4.3).
    const onEscape = vi.fn()
    createWrapper(
      '<div ref="containerRef" id="trap" tabindex="-1"><span>No buttons here</span></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })
    const prevented = vi.spyOn(event, 'preventDefault')
    document.dispatchEvent(event)

    expect(prevented).toHaveBeenCalled()
    expect(document.activeElement?.id).toBe('trap')
  })

  // Regression: the handler only intervened when `document.activeElement` was
  // exactly the first or last focusable, never asking whether focus was in the
  // trapped set at all. Focus legitimately sits outside that set in three
  // reachable states — on the container after mount, on the container after a
  // click on the dialog's own text, and on <body> after a control blurred
  // mid-request — and from each of them native Tab walked straight out to a
  // control behind the aria-modal dialog, where the virtual cursor cannot
  // describe it and nothing announces the exit (WCAG 2.4.3).
  describe('focus outside the trapped set', () => {
    const TRAP =
      '<div ref="containerRef" id="trap" tabindex="-1">' +
      '<button id="first">A</button><button id="last">B</button></div>'

    function tabFrom(shiftKey: boolean) {
      const event = new KeyboardEvent('keydown', {
        key: 'Tab',
        shiftKey,
        bubbles: true,
      })
      const prevented = vi.spyOn(event, 'preventDefault')
      document.dispatchEvent(event)
      return prevented
    }

    it('enters at the first focusable when Tab starts on the container', async () => {
      createWrapper(TRAP, vi.fn())
      await vi.runAllTimersAsync()
      expect(document.activeElement?.id).toBe('trap')

      const prevented = tabFrom(false)

      expect(prevented).toHaveBeenCalled()
      expect(document.activeElement?.id).toBe('first')
    })

    it('enters at the last focusable when Shift+Tab starts on the container', async () => {
      createWrapper(TRAP, vi.fn())
      await vi.runAllTimersAsync()
      expect(document.activeElement?.id).toBe('trap')

      const prevented = tabFrom(true)

      expect(prevented).toHaveBeenCalled()
      expect(document.activeElement?.id).toBe('last')
    })

    it('pulls focus back in when Tab starts outside the container', async () => {
      createWrapper(TRAP, vi.fn())
      await vi.runAllTimersAsync()
      const stray = document.createElement('button')
      document.body.appendChild(stray)
      stray.focus()
      expect(document.activeElement).toBe(stray)

      const prevented = tabFrom(false)

      expect(prevented).toHaveBeenCalled()
      expect(document.activeElement?.id).toBe('first')
      stray.remove()
    })
  })

  it('skips display:none elements when wrapping focus', async () => {
    // Regression: `v-show` hides with inline `display: none`, which still
    // matched the focusable selector. The hidden button became the "last"
    // stop, so Tab from the last VISIBLE control never wrapped.
    const onEscape = vi.fn()
    const wrapper = createWrapper(
      '<div ref="containerRef"><button id="first">A</button><button id="last">B</button>' +
        '<button id="ghost" style="display: none">C</button></div>',
      onEscape,
    )
    await vi.runAllTimersAsync()

    ;(wrapper.find('#last').element as HTMLElement).focus()
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })
    const prevented = vi.spyOn(event, 'preventDefault')
    document.dispatchEvent(event)

    expect(prevented).toHaveBeenCalled()
    expect(document.activeElement?.id).toBe('first')
  })

  it('skips visibility:hidden elements when wrapping focus', async () => {
    // The sibling of the `display: none` case: `isVisible` checks both, but
    // only `display` was covered, so the `visibility` half could have been
    // dropped with nothing failing. A `visibility: hidden` control is skipped
    // by the browser's own tab order too, so leaving it in the set would make
    // it a phantom last stop.
    const wrapper = createWrapper(
      '<div ref="containerRef"><button id="first">A</button><button id="last">B</button>' +
        '<button id="ghost" style="visibility: hidden">C</button></div>',
      vi.fn(),
    )
    await vi.runAllTimersAsync()

    ;(wrapper.find('#last').element as HTMLElement).focus()
    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })
    const prevented = vi.spyOn(event, 'preventDefault')
    document.dispatchEvent(event)

    expect(prevented).toHaveBeenCalled()
    expect(document.activeElement?.id).toBe('first')
  })

  // `summary:not([hidden])` is in the selector for the <details> disclosure in
  // the import modal, and `a[href]` for the links beside it. Every other test
  // here traps buttons only, so either entry could have been deleted from
  // FOCUSABLE_SELECTOR and the suite would still be green.
  describe('non-button focusables', () => {
    const MIXED =
      '<div ref="containerRef" id="trap" tabindex="-1">' +
      '<button id="first">A</button>' +
      '<details><summary id="disclosure">More</summary><p>detail</p></details>' +
      '<a id="link" href="#somewhere">Link</a></div>'

    function tab(shiftKey: boolean) {
      const event = new KeyboardEvent('keydown', {
        key: 'Tab',
        shiftKey,
        bubbles: true,
      })
      const prevented = vi.spyOn(event, 'preventDefault')
      document.dispatchEvent(event)
      return prevented
    }

    it('treats a link as the last stop in the wrap order', async () => {
      const wrapper = createWrapper(MIXED, vi.fn())
      await vi.runAllTimersAsync()
      ;(wrapper.find('#first').element as HTMLElement).focus()

      const prevented = tab(true)

      expect(prevented).toHaveBeenCalled()
      expect(document.activeElement?.id).toBe('link')
    })

    it('leaves Tab alone in the middle of the order, on the summary', async () => {
      const wrapper = createWrapper(MIXED, vi.fn())
      await vi.runAllTimersAsync()
      ;(wrapper.find('#disclosure').element as HTMLElement).focus()

      const prevented = tab(false)

      // The summary sits between the button and the link, so the trap has
      // nothing to do and the browser moves on natively. Were `summary`
      // missing from the selector it would count as focus outside the trapped
      // set, and the trap would yank focus back to the button instead.
      expect(prevented).not.toHaveBeenCalled()
      expect(document.activeElement?.id).toBe('disclosure')
    })
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
