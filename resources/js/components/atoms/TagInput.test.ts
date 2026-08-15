import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TagInput from './TagInput.vue'

describe('TagInput', () => {
  function mountInput(props = {}) {
    return mount(TagInput, {
      props: { modelValue: [], label: 'Genres', inputId: 'edit-genres', ...props },
    })
  }

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

  it('does not add an entry longer than 100 characters', async () => {
    const wrapper = mountInput()
    await wrapper.find('#edit-genres').setValue('x'.repeat(101))
    await wrapper.find('.add-rule-form button').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not remove or move focus while disabled', async () => {
    const wrapper = mountInput({ modelValue: ['a', 'b'], disabled: true })

    await wrapper.findAll('.tag-input-remove')[0].trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})
