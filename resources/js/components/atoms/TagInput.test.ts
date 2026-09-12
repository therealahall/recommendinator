import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import TagInput from './TagInput.vue'

describe('TagInput', () => {
  function mountInput(props = {}, attachTo?: HTMLElement) {
    return mount(TagInput, {
      props: { modelValue: [], label: 'Genres', inputId: 'edit-genres', ...props },
      attachTo,
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

  it('moves a tag down the list and announces where it landed', async () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'], reorderable: true })

    await wrapper.get('[aria-label="Move Sci-Fi down"]').trigger('click')

    expect(wrapper.emitted('update:modelValue')![0]).toEqual([['Drama', 'Sci-Fi']])
    await wrapper.setProps({ modelValue: ['Drama', 'Sci-Fi'] })
    expect(wrapper.get('[role="status"]').text()).toBe('Sci-Fi moved to position 2 of 2')
  })

  it('keeps focus on the move button once the tag reaches the top', async () => {
    const wrapper = mountInput(
      { modelValue: ['Sci-Fi', 'Drama'], reorderable: true },
      document.body,
    )

    await wrapper.get('[aria-label="Move Drama up"]').trigger('click')
    await wrapper.setProps({ modelValue: ['Drama', 'Sci-Fi'] })
    await nextTick()

    expect(document.activeElement?.getAttribute('aria-label')).toBe('Move Drama up')
    wrapper.unmount()
  })

  it('exposes the chips as a list so a reader can ask for a tag position', () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'], reorderable: true })

    expect(wrapper.get('[role="list"]').findAll('li').map((li) => li.text())).toEqual([
      expect.stringContaining('Sci-Fi'),
      expect.stringContaining('Drama'),
    ])
  })

  it('marks the arrow at each end of the list unavailable, not live', () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'], reorderable: true })

    expect(wrapper.get('[aria-label="Move Sci-Fi up"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.get('[aria-label="Move Drama down"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.get('[aria-label="Move Sci-Fi down"]').attributes('aria-disabled')).toBeUndefined()
  })

  it('does not move the first tag off the top of the list', async () => {
    const wrapper = mountInput({ modelValue: ['Sci-Fi', 'Drama'], reorderable: true })

    await wrapper.get('[aria-label="Move Sci-Fi up"]').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not remove a chip while disabled', async () => {
    const wrapper = mountInput({ modelValue: ['a', 'b'], disabled: true })

    await wrapper.findAll('.tag-input-remove')[0].trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})
