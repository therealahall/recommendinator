import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NumberStepper from './NumberStepper.vue'

describe('NumberStepper', () => {
  function mountStepper(props = {}) {
    return mount(NumberStepper, {
      props: { modelValue: 5, min: 1, max: 20, step: 1, ...props },
    })
  }

  it('renders current value in the input', () => {
    const wrapper = mountStepper({ modelValue: 7 })
    const input = wrapper.find('input')
    expect(input.element.value).toBe('7')
  })

  it('increments on + button click', async () => {
    const wrapper = mountStepper({ modelValue: 5 })
    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([6])
  })

  it('decrements on - button click', async () => {
    const wrapper = mountStepper({ modelValue: 5 })
    await wrapper.find('.stepper-decrement').trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([4])
  })

  it('does not emit below min when decrement button is disabled', async () => {
    const wrapper = mountStepper({ modelValue: 1, min: 1 })
    await wrapper.find('.stepper-decrement').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not emit above max when increment button is disabled', async () => {
    const wrapper = mountStepper({ modelValue: 20, max: 20 })
    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('disables decrement button at min', () => {
    const wrapper = mountStepper({ modelValue: 1, min: 1 })
    expect(wrapper.find('.stepper-decrement').attributes('disabled')).toBeDefined()
  })

  it('disables increment button at max', () => {
    const wrapper = mountStepper({ modelValue: 20, max: 20 })
    expect(wrapper.find('.stepper-increment').attributes('disabled')).toBeDefined()
  })

  it('parses valid integer from manual input', async () => {
    const wrapper = mountStepper()
    const input = wrapper.find('input')
    await input.setValue('10')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([10])
  })

  it('clamps manual input above max', async () => {
    const wrapper = mountStepper({ max: 20 })
    const input = wrapper.find('input')
    await input.setValue('999')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([20])
  })

  it('clamps manual input below min', async () => {
    const wrapper = mountStepper({ min: 1 })
    const input = wrapper.find('input')
    await input.setValue('-5')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([1])
  })

  it('does not emit on non-numeric input', async () => {
    const wrapper = mountStepper()
    const input = wrapper.find('input')
    await input.setValue('abc')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('applies aria-label to input and buttons via attribute', () => {
    const wrapper = mount(NumberStepper, {
      props: { modelValue: 5, min: 1, max: 20, step: 1 },
      attrs: { 'aria-label': 'Recommendation count' },
    })
    expect(wrapper.find('input').attributes('aria-label')).toBe('Recommendation count')
    expect(wrapper.find('.stepper-decrement').attributes('aria-label')).toBe('Decrease Recommendation count')
    expect(wrapper.find('.stepper-increment').attributes('aria-label')).toBe('Increase Recommendation count')
  })

  it('uses default aria-label when none provided', () => {
    const wrapper = mountStepper()
    expect(wrapper.find('input').attributes('aria-label')).toBe('Number')
    expect(wrapper.find('.stepper-decrement').attributes('aria-label')).toBe('Decrease Number')
    expect(wrapper.find('.stepper-increment').attributes('aria-label')).toBe('Increase Number')
  })
})
