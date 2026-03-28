import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ToggleSwitch from './ToggleSwitch.vue'

describe('ToggleSwitch', () => {
  it('renders with label text', () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: false, label: 'Show ignored' },
    })

    expect(wrapper.find('.toggle-switch-text').text()).toBe('Show ignored')
  })

  it('reflects off state via aria-checked', () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: false, label: 'Test' },
    })

    expect(wrapper.find('.toggle-switch').attributes('aria-checked')).toBe('false')
  })

  it('reflects on state via aria-checked', () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: true, label: 'Test' },
    })

    expect(wrapper.find('.toggle-switch').attributes('aria-checked')).toBe('true')
  })

  it('emits update:modelValue with toggled value on click', async () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: false, label: 'Test' },
    })

    await wrapper.find('.toggle-switch').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[true]])
  })

  it('emits false when toggling off', async () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: true, label: 'Test' },
    })

    await wrapper.find('.toggle-switch').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })

  it('uses switch role for accessibility', () => {
    const wrapper = mount(ToggleSwitch, {
      props: { modelValue: false, label: 'My Toggle' },
    })

    const toggle = wrapper.find('[role="switch"]')
    expect(toggle.exists()).toBe(true)
    expect(toggle.attributes('aria-label')).toBe('My Toggle')
  })
})
