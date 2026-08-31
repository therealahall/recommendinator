import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingControl from './SettingControl.vue'
import type { SettingViewValue } from '@/types/api'

// Fixtures name real registry leaves.
function value(overrides: Partial<SettingViewValue> = {}): SettingViewValue {
  return {
    key: 'enrichment.batch_size',
    section: 'enrichment',
    label: 'Batch size',
    help: 'How many items to enrich per pass',
    type: 'int',
    widget: 'number',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: false,
    value: 8000,
    db_overridden: false,
    ...overrides,
  }
}

function mountControl(
  setting: SettingViewValue,
  modelValue: string | number | boolean | string[],
  extra: Record<string, unknown> = {},
) {
  return mount(SettingControl, {
    props: { setting, modelValue, ...extra },
  })
}

function lastEmit(wrapper: ReturnType<typeof mountControl>, event: string): unknown[] {
  const events = wrapper.emitted(event)
  return events![events!.length - 1]
}

describe('SettingControl widget mapping', () => {
  it('passes through an absent max so the stepper renders unbounded', () => {
    // Regression: this binding is the seam the max-100 bug came through.
    const setting = value({
      type: 'int',
      widget: 'number',
      validation: { min: 1, max: null, max_length: null, pattern: null },
    })
    const wrapper = mountControl(setting, 8000)

    const input = wrapper.find('.stepper-input')
    expect(input.attributes('max')).toBeUndefined()
    expect(input.attributes('min')).toBe('1')
  })

  it('includes the current value as an option even when it is not in choices', () => {
    const setting = value({
      key: 'logging.level',
      type: 'enum',
      widget: 'select',
      choices: ['INFO', 'DEBUG'],
    })
    const wrapper = mountControl(setting, 'trace')
    const options = wrapper.findAll('option')
    expect(options.map((o) => o.text())).toContain('trace')
  })

  it('falls back on type when the widget is unknown', () => {
    const setting = value({ type: 'bool', widget: 'mystery' as never })
    const wrapper = mountControl(setting, false)
    expect(wrapper.find('[role="switch"]').exists()).toBe(true)
  })
})

describe('SettingControl badges and reset', () => {
  it('keeps Reset in the tab order while its request is in flight, and drops the second press', async () => {
    // `disabled` closed on the button the user had just pressed, blurring them
    // to <body> for the length of the request (WCAG 2.4.3).
    const wrapper = mount(SettingControl, {
      props: { setting: value({ db_overridden: true }), modelValue: 8000 },
      attachTo: document.body,
    })
    const button = wrapper.get('[data-testid="reset-enrichment.batch_size"]')
    const element = button.element as HTMLButtonElement
    element.focus()

    await button.trigger('click')
    await wrapper.setProps({ resetting: true })
    await button.trigger('click')

    expect(wrapper.emitted('reset')).toHaveLength(1)
    expect(document.activeElement).toBe(element)
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBe('true')
    wrapper.unmount()
  })

  it('keeps the words on the Reset button inside the name voice control matches', () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    const button = wrapper.get('[data-testid="reset-enrichment.batch_size"]')
    const name = button.attributes('aria-label') ?? button.text()

    expect(name).toContain('Reset to default')
    expect(name).toContain('Batch size')
  })

  it('says why a Reset locked by a section save cannot be used', async () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000, { disabled: true })
    const button = wrapper.get('[data-testid="reset-enrichment.batch_size"]')

    await button.trigger('click')

    expect(wrapper.emitted('reset')).toBeUndefined()
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.get(`[id="${button.attributes('aria-describedby')}"]`).text()).not.toBe('')
  })
})

describe('SettingControl value changes', () => {
  it('emits update:modelValue on text input', async () => {
    const setting = value({ key: 'logging.file', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'old')
    await wrapper.find('[data-testid="setting-logging.file"]').setValue('new')
    expect(lastEmit(wrapper, 'update:modelValue')).toEqual(['new'])
  })
})
