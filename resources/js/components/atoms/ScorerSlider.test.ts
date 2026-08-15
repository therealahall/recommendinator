import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ScorerSlider from './ScorerSlider.vue'

describe('ScorerSlider', () => {
  const defaultProps = { label: 'Popularity Bias', modelValue: 2.5 }

  it('renders "0.0" at zero so a disabled value reads as off, not blank', () => {
    // Zero formatting is label-agnostic; use the generic props so the atom
    // test stays domain-free.
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, modelValue: 0 },
    })
    expect(wrapper.find('.slider-value').text()).toBe('0.0')
    expect(wrapper.find('input[type="range"]').attributes('aria-valuetext')).toBe('0.0')
  })

  it('emits update:modelValue with parsed float on input', async () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    el.value = '4.2'
    await input.trigger('input')
    expect(wrapper.emitted('update:modelValue')).toEqual([[4.2]])
  })

  it('dismisses the tooltip on Escape (WCAG 1.4.13 Dismissable)', async () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipWrap.classes()).not.toContain('tooltip-dismissed')

    await tooltipWrap.trigger('keydown', { key: 'Escape' })
    expect(tooltipWrap.classes()).toContain('tooltip-dismissed')
  })
})
