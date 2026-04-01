import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ScorerSlider from './ScorerSlider.vue'

describe('ScorerSlider', () => {
  const defaultProps = { label: 'Popularity Bias', modelValue: 2.5 }

  it('range input has aria-labelledby matching the expected slugified id', () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    expect(input.attributes('aria-labelledby')).toBe('slider-label-popularity-bias')
  })

  it('label span id is a valid slugified id from the label prop', () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const labelSpan = wrapper.find('.slider-label')
    expect(labelSpan.attributes('id')).toBe('slider-label-popularity-bias')
  })

  it('aria-valuetext reflects the formatted display value', () => {
    const wrapper = mount(ScorerSlider, { props: { label: 'Test', modelValue: 3.7 } })
    const input = wrapper.find('input[type="range"]')
    expect(input.attributes('aria-valuetext')).toBe('3.7')
  })

  it('display value span has aria-hidden="true"', () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const valueSpan = wrapper.find('.slider-value')
    expect(valueSpan.attributes('aria-hidden')).toBe('true')
  })

  it('emits update:modelValue with parsed float on input', async () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    el.value = '4.2'
    await input.trigger('input')
    expect(wrapper.emitted('update:modelValue')).toEqual([[4.2]])
  })

  it('renders tooltip when tooltip prop is provided', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipWrap.exists()).toBe(true)
    expect(tooltipWrap.attributes('tabindex')).toBe('0')
  })

  it('does not render tooltip when tooltip prop is absent', () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    expect(wrapper.find('.scorer-tooltip-wrap').exists()).toBe(false)
  })

  it('strips non-alphanumeric characters from label id', () => {
    const wrapper = mount(ScorerSlider, {
      props: { label: 'Genre (Bias)', modelValue: 1.0 },
    })
    const labelSpan = wrapper.find('.slider-label')
    expect(labelSpan.attributes('id')).toBe('slider-label-genre-bias')
  })
})
