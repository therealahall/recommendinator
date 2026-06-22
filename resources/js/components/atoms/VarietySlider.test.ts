import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import VarietySlider from './VarietySlider.vue'

describe('VarietySlider', () => {
  const defaultProps = { label: 'Variety after completion', modelValue: 0.4 }

  it('range input has aria-labelledby matching the expected slugified id', () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    expect(input.attributes('aria-labelledby')).toBe('slider-label-variety-after-completion')
  })

  it('renders the stored float as a percentage', () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    expect(wrapper.find('.slider-value').text()).toBe('40%')
  })

  it('maps the 0.0-0.8 float onto the 0-80 range input', () => {
    const wrapper = mount(VarietySlider, { props: { label: 'Variety', modelValue: 0.8 } })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    expect(el.max).toBe('80')
    expect(el.value).toBe('80')
  })

  it('shows "Off" and aria-valuetext "Off" when value is 0', () => {
    const wrapper = mount(VarietySlider, { props: { label: 'Variety', modelValue: 0 } })
    expect(wrapper.find('.slider-value').text()).toBe('Off')
    expect(wrapper.find('input[type="range"]').attributes('aria-valuetext')).toBe('Off')
  })

  it('aria-valuetext reflects the percentage for non-zero values', () => {
    const wrapper = mount(VarietySlider, { props: { label: 'Variety', modelValue: 0.6 } })
    expect(wrapper.find('input[type="range"]').attributes('aria-valuetext')).toBe('60%')
  })

  it('display value span has aria-hidden="true"', () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    expect(wrapper.find('.slider-value').attributes('aria-hidden')).toBe('true')
  })

  it('emits update:modelValue as a 0.0-0.8 float on input', async () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    el.value = '60'
    await input.trigger('input')
    expect(wrapper.emitted('update:modelValue')).toEqual([[0.6]])
  })

  it('emits 0 when dragged to the off position', async () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    el.value = '0'
    await input.trigger('input')
    expect(wrapper.emitted('update:modelValue')).toEqual([[0]])
  })

  it('renders tooltip when tooltip prop is provided', () => {
    const wrapper = mount(VarietySlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipWrap.exists()).toBe(true)
    expect(tooltipWrap.attributes('tabindex')).toBe('0')
  })

  it('does not render tooltip when tooltip prop is absent', () => {
    const wrapper = mount(VarietySlider, { props: defaultProps })
    expect(wrapper.find('.scorer-tooltip-wrap').exists()).toBe(false)
  })

  it('associates the tooltip text with its trigger via aria-describedby', () => {
    const wrapper = mount(VarietySlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipText = wrapper.find('.scorer-tooltip-text')
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipText.attributes('id')).toBe('slider-label-variety-after-completion-tip')
    expect(tooltipWrap.attributes('aria-describedby')).toBe(
      'slider-label-variety-after-completion-tip',
    )
    expect(tooltipText.attributes('role')).toBe('tooltip')
  })

  it('renders the tooltip text content in the DOM so assistive tech can read it', () => {
    const wrapper = mount(VarietySlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    expect(wrapper.find('.scorer-tooltip-text').text()).toBe('A helpful tip')
  })
})
