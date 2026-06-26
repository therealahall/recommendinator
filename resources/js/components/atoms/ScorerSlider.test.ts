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

  it('renders "0.0" at zero so a disabled value reads as off, not blank', () => {
    // Zero formatting is label-agnostic; use the generic props so the atom
    // test stays domain-free.
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, modelValue: 0 },
    })
    expect(wrapper.find('.slider-value').text()).toBe('0.0')
    expect(wrapper.find('input[type="range"]').attributes('aria-valuetext')).toBe('0.0')
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

  it('emits 0 when dragged to the off position', async () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    const input = wrapper.find('input[type="range"]')
    const el = input.element as HTMLInputElement
    el.value = '0'
    await input.trigger('input')
    expect(wrapper.emitted('update:modelValue')).toEqual([[0]])
  })

  it('renders the tooltip trigger as a real button, not a focusable span', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipWrap.exists()).toBe(true)
    expect(tooltipWrap.element.tagName).toBe('BUTTON')
    expect(tooltipWrap.attributes('type')).toBe('button')
    // A native button is keyboard-focusable without a manual tabindex.
    expect(tooltipWrap.attributes('tabindex')).toBeUndefined()
  })

  it('does not render tooltip when tooltip prop is absent', () => {
    const wrapper = mount(ScorerSlider, { props: defaultProps })
    expect(wrapper.find('.scorer-tooltip-wrap').exists()).toBe(false)
  })

  it('names the tooltip button via aria-label so the "?" glyph is not announced', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipWrap.attributes('aria-label')).toBe('Popularity Bias info')
    expect(wrapper.find('.scorer-tooltip-icon').attributes('aria-hidden')).toBe('true')
  })

  it('keeps the range input accessible name exactly the label, not the tooltip', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    // aria-labelledby must point at a single node holding only the label text,
    // so the input announces "Popularity Bias" — not "... info A helpful tip".
    const input = wrapper.find('input[type="range"]')
    const labelledById = input.attributes('aria-labelledby')
    expect(labelledById).toBe('slider-label-popularity-bias')
    const labelEl = wrapper.get(`#${labelledById}`)
    expect(labelEl.text()).toBe('Popularity Bias')
    expect(labelEl.find('.scorer-tooltip-wrap').exists()).toBe(false)
  })

  it('associates the tooltip text with its trigger via aria-describedby', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipText = wrapper.find('.scorer-tooltip-text')
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')
    expect(tooltipText.attributes('id')).toBe('slider-label-popularity-bias-tip')
    expect(tooltipWrap.attributes('aria-describedby')).toBe('slider-label-popularity-bias-tip')
    expect(tooltipText.attributes('role')).toBe('tooltip')
  })

  it('renders the tooltip text content in the DOM so assistive tech can read it', () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    expect(wrapper.find('.scorer-tooltip-text').text()).toBe('A helpful tip')
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

  it('clears the dismissed state on mouseleave so the tooltip can show again', async () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')

    await tooltipWrap.trigger('keydown', { key: 'Escape' })
    expect(tooltipWrap.classes()).toContain('tooltip-dismissed')

    await tooltipWrap.trigger('mouseleave')
    expect(tooltipWrap.classes()).not.toContain('tooltip-dismissed')
  })

  it('clears the dismissed state on blur so a later focus can show it again', async () => {
    const wrapper = mount(ScorerSlider, {
      props: { ...defaultProps, tooltip: 'A helpful tip' },
    })
    const tooltipWrap = wrapper.find('.scorer-tooltip-wrap')

    await tooltipWrap.trigger('keydown', { key: 'Escape' })
    expect(tooltipWrap.classes()).toContain('tooltip-dismissed')

    await tooltipWrap.trigger('blur')
    expect(tooltipWrap.classes()).not.toContain('tooltip-dismissed')
  })

  it('strips non-alphanumeric characters from label id', () => {
    const wrapper = mount(ScorerSlider, {
      props: { label: 'Genre (Bias)', modelValue: 1.0 },
    })
    const labelSpan = wrapper.find('.slider-label')
    expect(labelSpan.attributes('id')).toBe('slider-label-genre-bias')
  })
})
