import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TypeSelect from './TypeSelect.vue'

describe('TypeSelect', () => {
  it('renders all options including All by default', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '' },
    })

    const options = wrapper.findAll('option')
    expect(options.map(o => o.text().trim())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('hides All option when includeAll is false', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: 'book', includeAll: false },
    })

    const options = wrapper.findAll('option')
    expect(options.map(o => o.text().trim())).toEqual(['Book', 'Movie', 'TV Show', 'Game'])
  })

  it('selects the option matching modelValue', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: 'movie' },
    })

    const select = wrapper.find('select').element as HTMLSelectElement
    expect(select.value).toBe('movie')
  })

  it('selects All when modelValue is empty string', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '' },
    })

    const select = wrapper.find('select').element as HTMLSelectElement
    expect(select.value).toBe('')
  })

  it('emits update:modelValue on change', async () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '' },
    })

    const select = wrapper.find('select')
    const el = select.element as HTMLSelectElement
    el.value = 'book'
    await select.trigger('change')

    expect(wrapper.emitted('update:modelValue')).toEqual([['book']])
  })

  it('has default aria-label', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '' },
    })

    expect(wrapper.find('select').attributes('aria-label')).toBe('Content type')
  })

  it('accepts custom aria-label', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '', ariaLabel: 'Filter type' },
    })

    expect(wrapper.find('select').attributes('aria-label')).toBe('Filter type')
  })

  it('reflects updated modelValue prop', async () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: 'book' },
    })

    await wrapper.setProps({ modelValue: 'tv_show' })

    const el = wrapper.find('select').element as HTMLSelectElement
    expect(el.value).toBe('tv_show')
  })

  it('emits empty string when All option is selected', async () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: 'book' },
    })

    const select = wrapper.find('select')
    const el = select.element as HTMLSelectElement
    el.value = ''
    await select.trigger('change')

    expect(wrapper.emitted('update:modelValue')).toEqual([['']])
  })

  it('sets correct value attributes on options', () => {
    const wrapper = mount(TypeSelect, {
      props: { modelValue: '', includeAll: false },
    })

    const options = wrapper.findAll('option')
    expect(options.map(o => o.attributes('value'))).toEqual(['book', 'movie', 'tv_show', 'video_game'])
  })
})
