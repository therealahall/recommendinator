import { describe, it, expect } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import TagInput from './TagInput.vue'

describe('TagInput', () => {
  function mountInput(props = {}) {
    return mount(TagInput, {
      props: { modelValue: [], label: 'Genres', inputId: 'edit-genres', ...props },
    })
  }

  it('renders existing tags as chips', () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'] })
    const chips = wrapper.findAll('.tag-input-chip')
    expect(chips.map((c) => c.text())).toEqual(['Sci-Fi ×', 'Drama ×'])
  })

  it('associates the input with its label', () => {
    const wrapper = mountInput()
    expect(wrapper.find('label[for="edit-genres"]').exists()).toBe(true)
    expect(wrapper.find('#edit-genres').exists()).toBe(true)
  })

  it('shows empty text when there are no tags', () => {
    const wrapper = mountInput({ emptyText: 'No genres yet' })
    expect(wrapper.find('.empty-rules').text()).toBe('No genres yet')
    expect(wrapper.find('.tag-input-chips').exists()).toBe(false)
  })

  it('emits updated list when adding via Add button', async () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi'] })
    await wrapper.find('#edit-genres').setValue('Drama')
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([['Sci-Fi', 'Drama']])
  })

  it('adds a tag when Enter is pressed in the input', async () => {
    const wrapper = mountInput()
    const input = wrapper.find('#edit-genres')
    await input.setValue('Drama')
    await input.trigger('keypress', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([['Drama']])
  })

  it('removes a tag when its remove button is clicked', async () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'] })
    await wrapper.findAll('.tag-input-remove')[0].trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([['Drama']])
  })

  it('trims whitespace and ignores empty input', async () => {
    const wrapper = mountInput()
    await wrapper.find('#edit-genres').setValue('   ')
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('de-dupes case-insensitively without emitting', async () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi'] })
    await wrapper.find('#edit-genres').setValue('sci-fi')
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('labels each remove button with the tag value', () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi'] })
    expect(wrapper.find('.tag-input-remove').attributes('aria-label')).toBe('Remove Sci-Fi')
  })

  it('add button is type="button" to avoid implicit submit', () => {
    const wrapper = mountInput()
    expect(wrapper.find('.add-rule-form button').attributes('type')).toBe('button')
  })

  it('caps the input at 100 characters via maxlength', () => {
    const wrapper = mountInput()
    expect(wrapper.find('#edit-genres').attributes('maxlength')).toBe('100')
  })

  it('does not add an entry longer than 100 characters', async () => {
    const wrapper = mountInput()
    await wrapper.find('#edit-genres').setValue('x'.repeat(101))
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('forwards aria-describedby and aria-invalid to the draft input', () => {
    const wrapper = mountInput({
      describedBy: 'help-cors err-cors',
      invalid: true,
    })

    const input = wrapper.find('#edit-genres')
    expect(input.attributes('aria-describedby')).toBe('help-cors err-cors')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  it('omits aria-invalid on the draft input when not invalid', () => {
    const wrapper = mountInput({ invalid: false })
    expect(wrapper.find('#edit-genres').attributes('aria-invalid')).toBeUndefined()
  })

  describe('focus after removing a chip', () => {
    // Regression: remove() emitted the shorter array and stopped. The × button
    // the user had just activated unmounted with its chip, so focus fell to
    // <body> and the next Tab restarted at the top of the document — once per
    // chip while pruning a list (WCAG 2.4.3).
    //
    // attachTo is required: focus() is inert on a detached element, so without
    // it every assertion here would read <body> and pass for the wrong reason.
    function mountAttached(props = {}) {
      return mount(TagInput, {
        props: { modelValue: [], label: 'Genres', inputId: 'edit-genres', ...props },
        attachTo: document.body,
      })
    }

    it('moves focus to the chip that took the removed one\'s place', async () => {
      const wrapper = mountAttached({ modelValue: ['a', 'b', 'c'] })
      const buttons = wrapper.findAll('.tag-input-remove')
      ;(buttons[0].element as HTMLElement).focus()

      await buttons[0].trigger('click')
      await wrapper.setProps({ modelValue: ['b', 'c'] })
      // The focus move waits a tick for the removed chip to leave the DOM.
      await flushPromises()

      const remaining = wrapper.findAll('.tag-input-remove')
      expect(document.activeElement).toBe(remaining[0].element)
      expect(remaining[0].attributes('aria-label')).toBe('Remove b')
      wrapper.unmount()
    })

    it('falls back to the previous chip when the last one is removed', async () => {
      const wrapper = mountAttached({ modelValue: ['a', 'b'] })
      const buttons = wrapper.findAll('.tag-input-remove')
      ;(buttons[1].element as HTMLElement).focus()

      await buttons[1].trigger('click')
      await wrapper.setProps({ modelValue: ['a'] })
      await flushPromises()

      const remaining = wrapper.findAll('.tag-input-remove')
      expect(document.activeElement).toBe(remaining[0].element)
      wrapper.unmount()
    })

    it('falls back to the draft input when the list empties', async () => {
      const wrapper = mountAttached({ modelValue: ['only'] })
      const button = wrapper.find('.tag-input-remove')
      ;(button.element as HTMLElement).focus()

      await button.trigger('click')
      await wrapper.setProps({ modelValue: [] })
      await flushPromises()

      expect(document.activeElement).toBe(wrapper.find('#edit-genres').element)
      wrapper.unmount()
    })

    it('does not remove or move focus while disabled', async () => {
      const wrapper = mountAttached({ modelValue: ['a', 'b'], disabled: true })

      await wrapper.findAll('.tag-input-remove')[0].trigger('click')

      expect(wrapper.emitted('update:modelValue')).toBeUndefined()
      wrapper.unmount()
    })
  })
})
