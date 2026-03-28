import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TypePills from './TypePills.vue'

describe('TypePills', () => {
  it('renders all options including All by default', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('hides All pill when includeAll is false', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book', includeAll: false },
    })

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['Book', 'Movie', 'TV Show', 'Game'])
  })

  it('marks the active pill', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'movie' },
    })

    const pills = wrapper.findAll('.pill')
    const moviePill = pills.find(p => p.text() === 'Movie')!
    expect(moviePill.classes()).toContain('active')

    const bookPill = pills.find(p => p.text() === 'Book')!
    expect(bookPill.classes()).not.toContain('active')
  })

  it('marks All pill as active when modelValue is empty string', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const allPill = wrapper.findAll('.pill').find(p => p.text() === 'All')!
    expect(allPill.classes()).toContain('active')
  })

  it('emits update:modelValue on click', async () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    const bookPill = wrapper.findAll('.pill').find(p => p.text() === 'Book')!
    await bookPill.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['book']])
  })

  it('uses radiogroup and radio roles for accessibility', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book' },
    })

    expect(wrapper.find('[role="radiogroup"]').exists()).toBe(true)
    const radios = wrapper.findAll('[role="radio"]')
    expect(radios.length).toBe(5)

    const bookRadio = radios.find(r => r.text() === 'Book')!
    expect(bookRadio.attributes('aria-checked')).toBe('true')

    const movieRadio = radios.find(r => r.text() === 'Movie')!
    expect(movieRadio.attributes('aria-checked')).toBe('false')
  })

  it('has default aria-label on radiogroup', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '' },
    })

    expect(wrapper.find('[role="radiogroup"]').attributes('aria-label')).toBe('Content type')
  })

  it('accepts custom aria-label', () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: '', ariaLabel: 'Enrichment type' },
    })

    expect(wrapper.find('[role="radiogroup"]').attributes('aria-label')).toBe('Enrichment type')
  })

  it('emits on click even when clicking the already-active pill', async () => {
    const wrapper = mount(TypePills, {
      props: { modelValue: 'book' },
    })

    const bookPill = wrapper.findAll('.pill').find(p => p.text() === 'Book')!
    await bookPill.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['book']])
  })
})
