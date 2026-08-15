import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SearchInput from './SearchInput.vue'

describe('SearchInput', () => {
  it('emits update:modelValue on input', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: '' } })
    const input = wrapper.find('input')
    await input.setValue('dune')
    expect(wrapper.emitted('update:modelValue')).toEqual([['dune']])
  })

  it('clear button emits empty modelValue then clear', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
    await wrapper.find('.search-input-clear').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
    expect(wrapper.emitted('clear')).toEqual([[]])
  })

  it('Escape clears when text is present and prevents default', async () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc' } })
    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
    expect(wrapper.emitted('clear')).toEqual([[]])
  })

  it('shows a spinner and hides the clear button while loading', () => {
    const wrapper = mount(SearchInput, { props: { modelValue: 'abc', loading: true } })
    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(wrapper.find('.search-input-clear').exists()).toBe(false)
  })

  describe('character limit', () => {
    // Regression: the input carried no bound, so a term longer than the one the
    // API accepts came back 422 — a bare status line over an emptied list where
    // the user used to get the ordinary "no items match" empty state.
    it('bounds the input and stays quiet below the limit', () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abcd', maxlength: 5 } })
      expect(wrapper.find('input').attributes('maxlength')).toBe('5')
      expect(wrapper.find('.search-input-limit').exists()).toBe(false)
      expect(wrapper.find('input').attributes('aria-describedby')).toBeUndefined()
    })

    it('describes the input with a visible notice that does not announce twice', () => {
      const wrapper = mount(SearchInput, { props: { modelValue: 'abcde', maxlength: 5 } })
      const notice = wrapper.find('.search-input-limit')
      expect(notice.text()).toBe(
        'Search is limited to 5 characters. Anything longer is not included.',
      )
      expect(notice.attributes('role')).toBeUndefined()
      expect(notice.attributes('aria-live')).toBeUndefined()
      expect(notice.attributes('id')).toBe('library-search-limit')
      expect(wrapper.find('input').attributes('aria-describedby')).toBe('library-search-limit')
    })
  })
})
