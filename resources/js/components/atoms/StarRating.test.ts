import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StarRating from './StarRating.vue'

describe('StarRating', () => {
  it('renders 5 star buttons', () => {
    const wrapper = mount(StarRating, { props: { modelValue: null } })
    const stars = wrapper.findAll('.star-rating-star')
    expect(stars.length).toBe(5)
    stars.forEach((star) => {
      expect(star.element.tagName).toBe('BUTTON')
    })
  })

  it('star buttons have correct aria-label', () => {
    const wrapper = mount(StarRating, { props: { modelValue: null } })
    const stars = wrapper.findAll('.star-rating-star')
    expect(stars[0].attributes('aria-label')).toBe('1 star')
    expect(stars[1].attributes('aria-label')).toBe('2 stars')
    expect(stars[4].attributes('aria-label')).toBe('5 stars')
  })

  it('sets aria-pressed true on stars at or below current rating', () => {
    const wrapper = mount(StarRating, { props: { modelValue: 3 } })
    const stars = wrapper.findAll('.star-rating-star')
    expect(stars[0].attributes('aria-pressed')).toBe('true')
    expect(stars[1].attributes('aria-pressed')).toBe('true')
    expect(stars[2].attributes('aria-pressed')).toBe('true')
    expect(stars[3].attributes('aria-pressed')).toBe('false')
    expect(stars[4].attributes('aria-pressed')).toBe('false')
  })

  it('all stars are aria-pressed false when modelValue is null', () => {
    const wrapper = mount(StarRating, { props: { modelValue: null } })
    const stars = wrapper.findAll('.star-rating-star')
    stars.forEach((star) => {
      expect(star.attributes('aria-pressed')).toBe('false')
    })
  })

  it('emits update:modelValue with star value on click', async () => {
    const wrapper = mount(StarRating, { props: { modelValue: null } })
    await wrapper.findAll('.star-rating-star')[2].trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[3]])
  })

  it('emits null on Clear button click', async () => {
    const wrapper = mount(StarRating, { props: { modelValue: 4 } })
    await wrapper.find('.btn-clear-rating').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[null]])
  })

  it('container has role="group" with aria-label when no ariaLabelledby', () => {
    const wrapper = mount(StarRating, { props: { modelValue: null } })
    const group = wrapper.find('.star-rating')
    expect(group.attributes('role')).toBe('group')
    expect(group.attributes('aria-label')).toBe('Rating')
    expect(group.attributes('aria-labelledby')).toBeUndefined()
  })

  it('uses aria-labelledby instead of aria-label when prop is provided', () => {
    const wrapper = mount(StarRating, {
      props: { modelValue: null, ariaLabelledby: 'external-label' },
    })
    const group = wrapper.find('.star-rating')
    expect(group.attributes('aria-labelledby')).toBe('external-label')
    expect(group.attributes('aria-label')).toBeUndefined()
  })
})
