import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SettingControl from './SettingControl.vue'
import type { SettingViewValue } from '@/types/api'

function value(overrides: Partial<SettingViewValue> = {}): SettingViewValue {
  return {
    key: 'web.port',
    section: 'web',
    label: 'Port',
    help: 'The port to bind',
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
  it('renders a ToggleSwitch for a toggle widget', () => {
    const setting = value({ type: 'bool', widget: 'toggle', label: 'Debug' })
    const wrapper = mountControl(setting, true)
    expect(wrapper.find('[role="switch"]').exists()).toBe(true)
    expect(wrapper.find('[role="switch"]').attributes('aria-checked')).toBe('true')
  })

  it('renders a NumberStepper for an int number widget', () => {
    const wrapper = mountControl(value({ type: 'int', widget: 'number' }), 8000)
    expect(wrapper.find('.number-stepper').exists()).toBe(true)
  })

  it('renders a step="any" number input for a float number widget', () => {
    const setting = value({ type: 'float', widget: 'number', label: 'Rate' })
    const wrapper = mountControl(setting, 0.5)
    const input = wrapper.find('[data-testid="setting-web.port"]')
    expect(input.attributes('type')).toBe('number')
    expect(input.attributes('step')).toBe('any')
  })

  it('renders a text input honoring max_length and pattern', () => {
    const setting = value({
      key: 'web.host',
      type: 'string',
      widget: 'text',
      label: 'Host',
      validation: { min: null, max: null, max_length: 20, pattern: '[a-z.]+' },
    })
    const wrapper = mountControl(setting, 'localhost')
    const input = wrapper.find('[data-testid="setting-web.host"]')
    expect(input.attributes('type')).toBe('text')
    expect(input.attributes('maxlength')).toBe('20')
    expect(input.attributes('pattern')).toBe('[a-z.]+')
  })

  it('renders a select with options from choices for a select widget', () => {
    const setting = value({
      key: 'log.level',
      type: 'enum',
      widget: 'select',
      label: 'Level',
      choices: ['info', 'debug'],
    })
    const wrapper = mountControl(setting, 'info')
    const options = wrapper.findAll('option')
    expect(options.map((o) => o.text())).toEqual(['info', 'debug'])
  })

  it('includes the current value as an option even when it is not in choices', () => {
    const setting = value({
      key: 'log.level',
      type: 'enum',
      widget: 'select',
      choices: ['info', 'debug'],
    })
    const wrapper = mountControl(setting, 'trace')
    const options = wrapper.findAll('option')
    expect(options.map((o) => o.text())).toContain('trace')
  })

  it('renders a TagInput for a tags widget', () => {
    const setting = value({
      key: 'cors.origins',
      type: 'list',
      widget: 'tags',
      label: 'Origins',
    })
    const wrapper = mountControl(setting, ['https://a.example'])
    expect(wrapper.find('.tag-input').exists()).toBe(true)
  })

  it('falls back on type when the widget is unknown', () => {
    const setting = value({ type: 'bool', widget: 'mystery' as never })
    const wrapper = mountControl(setting, false)
    expect(wrapper.find('[role="switch"]').exists()).toBe(true)
  })
})

describe('SettingControl badges and reset', () => {
  it('shows the Requires restart badge only when restart_required', () => {
    const on = mountControl(value({ restart_required: true }), 8000)
    expect(on.find('[data-testid="restart-badge-web.port"]').text()).toContain('Requires restart')
    const off = mountControl(value({ restart_required: false }), 8000)
    expect(off.find('[data-testid="restart-badge-web.port"]').exists()).toBe(false)
  })

  it('shows the Overridden badge and Reset button only when db_overridden', () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    expect(wrapper.find('[data-testid="overridden-badge-web.port"]').text()).toContain('Overridden')
    const reset = wrapper.find('[data-testid="reset-web.port"]')
    expect(reset.exists()).toBe(true)
    expect(reset.attributes('aria-label')).toBe('Reset Port to default')
  })

  it('hides the Reset button when not overridden', () => {
    const wrapper = mountControl(value({ db_overridden: false }), 8000)
    expect(wrapper.find('[data-testid="reset-web.port"]').exists()).toBe(false)
  })

  it('emits reset when the Reset button is clicked', async () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    await wrapper.find('[data-testid="reset-web.port"]').trigger('click')
    expect(wrapper.emitted('reset')).toHaveLength(1)
  })
})

describe('SettingControl validation error', () => {
  it('renders the error with role=alert and marks the control invalid', () => {
    const setting = value({ key: 'web.host', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'x', { error: 'bad value' })
    const err = wrapper.find('[data-testid="setting-error-web.host"]')
    expect(err.text()).toBe('bad value')
    expect(err.attributes('role')).toBe('alert')
    const input = wrapper.find('[data-testid="setting-web.host"]')
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(input.attributes('aria-describedby')).toContain('err-web.host')
  })

  it('renders no error block when error is empty', () => {
    const setting = value({ key: 'web.host', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'x')
    expect(wrapper.find('[data-testid="setting-error-web.host"]').exists()).toBe(false)
  })
})

describe('SettingControl forwards a11y hooks to the focusable atom', () => {
  it('wires id, aria-describedby, and aria-invalid onto the toggle switch', () => {
    const setting = value({
      key: 'web.debug',
      type: 'bool',
      widget: 'toggle',
      label: 'Debug',
      help: 'Verbose logs',
    })
    const wrapper = mountControl(setting, false, { error: 'nope' })
    const sw = wrapper.find('[role="switch"]')
    expect(sw.attributes('id')).toBe('setting-web.debug')
    expect(sw.attributes('aria-describedby')).toContain('help-web.debug')
    expect(sw.attributes('aria-describedby')).toContain('err-web.debug')
    expect(sw.attributes('aria-invalid')).toBe('true')
  })

  it('wires id, aria-describedby, and aria-invalid onto the number stepper input', () => {
    const wrapper = mountControl(value({ help: 'Port to bind' }), 8000, { error: 'bad' })
    const input = wrapper.find('.stepper-input')
    expect(input.attributes('id')).toBe('setting-web.port')
    expect(input.attributes('aria-describedby')).toContain('help-web.port')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  it('wires aria-describedby and aria-invalid onto the tags draft input', () => {
    const setting = value({
      key: 'cors.origins',
      type: 'list',
      widget: 'tags',
      label: 'Origins',
      help: 'Allowed origins',
    })
    const wrapper = mountControl(setting, [], { error: 'bad' })
    const input = wrapper.find('#setting-cors\\.origins')
    expect(input.attributes('aria-describedby')).toContain('help-cors.origins')
    expect(input.attributes('aria-invalid')).toBe('true')
  })
})

describe('SettingControl value changes', () => {
  it('emits update:modelValue on text input', async () => {
    const setting = value({ key: 'web.host', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'old')
    await wrapper.find('[data-testid="setting-web.host"]').setValue('new')
    expect(lastEmit(wrapper, 'update:modelValue')).toEqual(['new'])
  })

  it('emits update:modelValue on select change', async () => {
    const setting = value({
      key: 'log.level',
      type: 'enum',
      widget: 'select',
      choices: ['info', 'debug'],
    })
    const wrapper = mountControl(setting, 'info')
    await wrapper.find('[data-testid="setting-log.level"]').setValue('debug')
    expect(lastEmit(wrapper, 'update:modelValue')).toEqual(['debug'])
  })
})
