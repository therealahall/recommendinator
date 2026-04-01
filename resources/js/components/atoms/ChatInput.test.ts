import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from './ChatInput.vue'

describe('ChatInput', () => {
  it('textarea has aria-label', () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const textarea = wrapper.find('textarea')
    expect(textarea.attributes('aria-label')).toBe('Message to assistant')
  })

  it('send button has aria-label', () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const btn = wrapper.find('.chat-send-btn')
    expect(btn.attributes('aria-label')).toBe('Send message')
  })

  it('SVG inside send button has aria-hidden', () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const svg = wrapper.find('.chat-send-btn svg')
    expect(svg.attributes('aria-hidden')).toBe('true')
  })

  it('Enter key emits send with trimmed text', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('  hello world  ')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toEqual([['hello world']])
  })

  it('Shift+Enter does not submit', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('hello')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('does not emit when input is whitespace-only', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    const textarea = wrapper.find('textarea')
    await textarea.setValue('   ')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('click on send button emits send with trimmed text', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: false } })
    await wrapper.find('textarea').setValue('  clicked  ')
    await wrapper.find('.chat-send-btn').trigger('click')
    expect(wrapper.emitted('send')).toEqual([['clicked']])
  })

  it('disables textarea and button when disabled prop is true', () => {
    const wrapper = mount(ChatInput, { props: { disabled: true } })
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.chat-send-btn').attributes('disabled')).toBeDefined()
  })
})
