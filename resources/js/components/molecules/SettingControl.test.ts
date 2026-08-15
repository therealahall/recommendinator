import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingControl from './SettingControl.vue'
import type { SettingViewValue } from '@/types/api'

// Fixtures name real registry leaves. The web bind settings used to be the
// fixtures here, but they are bootstrap-only now and absent from the registry —
// a fixture keyed on a deleted leaf reads as documentation of a surface that no
// longer exists.
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
    // NumberStepper used to default `max` to 100, so `validation.max: null`
    // arriving here as `undefined` silently capped every min-only setting.
    // The atom's own tests cannot catch a regression in this wiring.
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
  it('emits reset when the Reset button is clicked', async () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    await wrapper.find('[data-testid="reset-enrichment.batch_size"]').trigger('click')
    expect(wrapper.emitted('reset')).toHaveLength(1)
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
