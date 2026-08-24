import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ModalDialog from './ModalDialog.vue'

function open(attachTo?: HTMLElement) {
  return mount(ModalDialog, {
    attachTo,
    props: { labelledBy: 'dialog-heading' },
    slots: {
      default: '<h2 id="dialog-heading">Heading</h2><input id="field">',
      actions: '<button id="cancel">Cancel</button><button id="confirm">Save</button>',
    },
  })
}

describe('ModalDialog', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('announces itself as a modal dialog named by the caller heading', () => {
    const wrapper = open()
    const surface = wrapper.get('[role="dialog"]')

    expect(surface.attributes('aria-modal')).toBe('true')
    expect(surface.attributes('aria-labelledby')).toBe('dialog-heading')
    wrapper.unmount()
  })

  it('wraps Tab from the last control back inside instead of onto the page behind', async () => {
    const wrapper = open(document.body)
    await vi.runAllTimersAsync()
    ;(wrapper.get('#confirm').element as HTMLElement).focus()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))

    expect(wrapper.get('[role="dialog"]').element.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(wrapper.get('#field').element)
    wrapper.unmount()
  })

  it('gives the keyboard back to whatever opened it', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const wrapper = open(document.body)
    await vi.runAllTimersAsync()
    expect(document.activeElement).not.toBe(opener)

    wrapper.unmount()

    expect(document.activeElement).toBe(opener)
    opener.remove()
  })

  it('asks to dismiss on Escape and on the backdrop, but not from inside', async () => {
    const wrapper = open(document.body)
    await vi.runAllTimersAsync()

    await wrapper.get('[role="dialog"]').trigger('click')
    expect(wrapper.emitted('dismiss')).toBeFalsy()

    await wrapper.trigger('click')
    expect(wrapper.emitted('dismiss')).toHaveLength(1)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('dismiss')).toHaveLength(2)
    wrapper.unmount()
  })
})
