import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NumberStepper from './NumberStepper.vue'

describe('NumberStepper', () => {
  function mountStepper(props = {}) {
    return mount(NumberStepper, {
      props: { modelValue: 5, min: 1, max: 20, step: 1, ...props },
    })
  }

  describe('when max is omitted', () => {
    function mountUnbounded(props = {}) {
      return mount(NumberStepper, {
        props: { modelValue: 0, min: 0, step: 1, ...props },
      })
    }

    it('does not clamp typed input', async () => {
      const wrapper = mountUnbounded()
      const input = wrapper.find('input')
      input.element.value = '8192'
      await input.trigger('input')
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([8192])
    })

    it('leaves the increment button operable above the old default cap', () => {
      const button = mountUnbounded({ modelValue: 8192 }).find('.stepper-increment')
      expect(button.attributes('aria-disabled')).toBeUndefined()
      expect(button.attributes('disabled')).toBeUndefined()
    })
  })

  it('shows no number until the caller has one, and the first press names the minimum', async () => {
    const wrapper = mountStepper({ modelValue: null, min: 3 })

    expect(wrapper.find('input').element.value).toBe('')

    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([3])
  })

  it('increments on + button click', async () => {
    const wrapper = mountStepper({ modelValue: 5 })
    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')![0]).toEqual([6])
  })

  it('does not emit above max when the increment button is at the max bound', async () => {
    const wrapper = mountStepper({ modelValue: 20, max: 20 })
    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  describe('when min is omitted', () => {
    function mountNoBounds(props = {}) {
      return mount(NumberStepper, { props: { modelValue: 0, step: 1, ...props } })
    }

    it('does not clamp below zero', async () => {
      const wrapper = mountNoBounds()
      const input = wrapper.find('input')
      input.element.value = '-5'
      await input.trigger('input')
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([-5])
    })
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
})
