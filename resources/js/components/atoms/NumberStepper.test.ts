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

  describe('when max is omitted', () => {
    // Regression: `max` carried a withDefaults value of 100, and SettingControl
    // passes `validation?.max ?? undefined` — so every settings leaf declaring a
    // min with no max silently clamped to 100. conversation.llm.max_tokens
    // (default 2000, min 1, no max) is the clearest case: its default already sat
    // above the cap, so any edit snapped it to 100 with no error, no announcement,
    // and a spinbutton reporting a maximum the registry never declared.
    function mountUnbounded(props = {}) {
      return mount(NumberStepper, {
        props: { modelValue: 0, min: 0, step: 1, ...props },
      })
    }

    it('renders no max attribute', () => {
      expect(mountUnbounded().find('input').attributes('max')).toBeUndefined()
    })

    it('does not clamp typed input', async () => {
      const wrapper = mountUnbounded()
      const input = wrapper.find('input')
      input.element.value = '8192'
      await input.trigger('input')
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([8192])
    })

    it('leaves the increment button operable above the old default cap', () => {
      // Asserts aria-disabled, not disabled. Bound state moved to aria-disabled,
      // so a `disabled`-only assertion here would be unconditionally undefined
      // (native disabled binds solely to props.disabled, which this never sets)
      // and would still pass with `max: 100` reinstated — testing nothing.
      const button = mountUnbounded({ modelValue: 8192 }).find('.stepper-increment')
      expect(button.attributes('aria-disabled')).toBeUndefined()
      expect(button.attributes('disabled')).toBeUndefined()
    })

    it('still enforces min', async () => {
      const wrapper = mountUnbounded({ modelValue: 0, min: 2 })
      const input = wrapper.find('input')
      input.element.value = '-5'
      await input.trigger('input')
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([2])
    })
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

  // The bound buttons are aria-disabled, not natively disabled, so the browser
  // still delivers the click — these prove the handler guards drop it.
  it('does not emit below min when the decrement button is at the min bound', async () => {
    const wrapper = mountStepper({ modelValue: 1, min: 1 })
    await wrapper.find('.stepper-decrement').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not emit above max when the increment button is at the max bound', async () => {
    const wrapper = mountStepper({ modelValue: 20, max: 20 })
    await wrapper.find('.stepper-increment').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('marks decrement aria-disabled at min, without removing it from focus', () => {
    // Regression: native `disabled` at the bound blurred the button the user
    // was operating — stepping down to min dropped focus to <body> mid-press
    // (WCAG 2.4.3). aria-disabled conveys the state and keeps it focusable;
    // the handler guards activation.
    const button = mountStepper({ modelValue: 1, min: 1 }).find('.stepper-decrement')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('marks increment aria-disabled at max, without removing it from focus', () => {
    const button = mountStepper({ modelValue: 20, max: 20 }).find('.stepper-increment')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  describe('disabled prop (save in flight)', () => {
    // Native disabled is correct HERE: reaching Save requires focusing Save, so
    // focus can never be inside the stepper when this flips.
    it('natively disables the input and both buttons', () => {
      const wrapper = mountStepper({ disabled: true })
      expect(wrapper.find('.stepper-input').attributes('disabled')).toBeDefined()
      expect(wrapper.find('.stepper-decrement').attributes('disabled')).toBeDefined()
      expect(wrapper.find('.stepper-increment').attributes('disabled')).toBeDefined()
    })

    it('emits nothing when activated while disabled', async () => {
      const wrapper = mountStepper({ disabled: true })
      await wrapper.find('.stepper-increment').trigger('click')
      await wrapper.find('.stepper-decrement').trigger('click')
      expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    })
  })

  describe('when min is omitted', () => {
    // The mirror of the max case: `withDefaults` dropped BOTH bounds, so a
    // reintroduced `min: 1` default would break conversation.llm.context_window_size
    // (min 0, default 0) with the suite green.
    function mountNoBounds(props = {}) {
      return mount(NumberStepper, { props: { modelValue: 0, step: 1, ...props } })
    }

    it('renders no min attribute', () => {
      expect(mountNoBounds().find('input').attributes('min')).toBeUndefined()
    })

    it('does not clamp below zero', async () => {
      const wrapper = mountNoBounds()
      const input = wrapper.find('input')
      input.element.value = '-5'
      await input.trigger('input')
      expect(wrapper.emitted('update:modelValue')![0]).toEqual([-5])
    })

    it('leaves decrement operable at zero', () => {
      const button = mountNoBounds().find('.stepper-decrement')
      expect(button.attributes('aria-disabled')).toBeUndefined()
      expect(button.attributes('disabled')).toBeUndefined()
    })
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

  it('forwards id, aria-describedby, and aria-invalid to the number input', () => {
    const wrapper = mountStepper({
      id: 'setting-web.port',
      describedBy: 'help-web.port err-web.port',
      invalid: true,
    })

    const input = wrapper.find('input')
    expect(input.attributes('id')).toBe('setting-web.port')
    expect(input.attributes('aria-describedby')).toBe('help-web.port err-web.port')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  it('omits aria-invalid on the input when not invalid', () => {
    const wrapper = mountStepper({ invalid: false })
    expect(wrapper.find('input').attributes('aria-invalid')).toBeUndefined()
  })
})
