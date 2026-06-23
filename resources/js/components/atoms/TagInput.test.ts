import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
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
})
