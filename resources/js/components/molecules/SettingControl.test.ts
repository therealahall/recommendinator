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

  it('passes through a declared max', () => {
    const setting = value({
      type: 'int',
      widget: 'number',
      validation: { min: 0, max: 131072, max_length: null, pattern: null },
    })
    const wrapper = mountControl(setting, 4096)

    expect(wrapper.find('.stepper-input').attributes('max')).toBe('131072')
  })

  it('renders a step="any" number input for a float number widget', () => {
    // A real float leaf: the base fixture is enrichment.batch_size, which the
    // registry declares as int.
    const setting = value({
      key: 'recommendations.scorer_weights.genre_match',
      type: 'float',
      widget: 'number',
      label: 'Genre match weight',
    })
    const wrapper = mountControl(setting, 0.5)
    const input = wrapper.find('[data-testid="setting-recommendations.scorer_weights.genre_match"]')
    expect(input.attributes('type')).toBe('number')
    expect(input.attributes('step')).toBe('any')
  })

  it('renders a text input honoring max_length and pattern', () => {
    const setting = value({
      key: 'logging.file',
      type: 'string',
      widget: 'text',
      label: 'Host',
      validation: { min: null, max: null, max_length: 20, pattern: '[a-z.]+' },
    })
    const wrapper = mountControl(setting, 'localhost')
    const input = wrapper.find('[data-testid="setting-logging.file"]')
    expect(input.attributes('type')).toBe('text')
    expect(input.attributes('maxlength')).toBe('20')
    expect(input.attributes('pattern')).toBe('[a-z.]+')
  })

  it('renders a select with options from choices for a select widget', () => {
    const setting = value({
      key: 'logging.level',
      type: 'enum',
      widget: 'select',
      label: 'Level',
      choices: ['INFO', 'DEBUG'],
    })
    const wrapper = mountControl(setting, 'INFO')
    const options = wrapper.findAll('option')
    expect(options.map((o) => o.text())).toEqual(['INFO', 'DEBUG'])
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

  it('renders a TagInput for a tags widget', () => {
    const setting = value({
      key: 'web.allowed_origins',
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
    expect(on.find('[data-testid="restart-badge-enrichment.batch_size"]').text()).toContain('Requires restart')
    const off = mountControl(value({ restart_required: false }), 8000)
    expect(off.find('[data-testid="restart-badge-enrichment.batch_size"]').exists()).toBe(false)
  })

  it('shows the Overridden badge and Reset button only when db_overridden', () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    expect(wrapper.find('[data-testid="overridden-badge-enrichment.batch_size"]').text()).toContain('Overridden')
    const reset = wrapper.find('[data-testid="reset-enrichment.batch_size"]')
    expect(reset.exists()).toBe(true)
    expect(reset.attributes('aria-label')).toBe('Reset Batch size to default')
  })

  it('hides the Reset button when not overridden', () => {
    const wrapper = mountControl(value({ db_overridden: false }), 8000)
    expect(wrapper.find('[data-testid="reset-enrichment.batch_size"]').exists()).toBe(false)
  })

  it('emits reset when the Reset button is clicked', async () => {
    const wrapper = mountControl(value({ db_overridden: true }), 8000)
    await wrapper.find('[data-testid="reset-enrichment.batch_size"]').trigger('click')
    expect(wrapper.emitted('reset')).toHaveLength(1)
  })
})

describe('SettingControl validation error', () => {
  it('renders the error with role=alert and marks the control invalid', () => {
    const setting = value({ key: 'logging.file', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'x', { error: 'bad value' })
    const err = wrapper.find('[data-testid="setting-error-logging.file"]')
    expect(err.text()).toBe('bad value')
    expect(err.attributes('role')).toBe('alert')
    const input = wrapper.find('[data-testid="setting-logging.file"]')
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(input.attributes('aria-describedby')).toContain('err-logging.file')
  })

  it('renders no error block when error is empty', () => {
    const setting = value({ key: 'logging.file', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'x')
    expect(wrapper.find('[data-testid="setting-error-logging.file"]').exists()).toBe(false)
  })
})

describe('SettingControl forwards a11y hooks to the focusable atom', () => {
  it('wires id, aria-describedby, and aria-invalid onto the toggle switch', () => {
    const setting = value({
      key: 'enrichment.enabled',
      type: 'bool',
      widget: 'toggle',
      label: 'Debug',
      help: 'Verbose logs',
    })
    const wrapper = mountControl(setting, false, { error: 'nope' })
    const sw = wrapper.find('[role="switch"]')
    expect(sw.attributes('id')).toBe('setting-enrichment.enabled')
    expect(sw.attributes('aria-describedby')).toContain('help-enrichment.enabled')
    expect(sw.attributes('aria-describedby')).toContain('err-enrichment.enabled')
    expect(sw.attributes('aria-invalid')).toBe('true')
  })

  it('wires id, aria-describedby, and aria-invalid onto the number stepper input', () => {
    const wrapper = mountControl(value({ help: 'Port to bind' }), 8000, { error: 'bad' })
    const input = wrapper.find('.stepper-input')
    expect(input.attributes('id')).toBe('setting-enrichment.batch_size')
    expect(input.attributes('aria-describedby')).toContain('help-enrichment.batch_size')
    expect(input.attributes('aria-invalid')).toBe('true')
  })

  it('wires aria-describedby and aria-invalid onto the tags draft input', () => {
    const setting = value({
      key: 'web.allowed_origins',
      type: 'list',
      widget: 'tags',
      label: 'Origins',
      help: 'Allowed origins',
    })
    const wrapper = mountControl(setting, [], { error: 'bad' })
    const input = wrapper.find('#setting-web\\.allowed_origins')
    expect(input.attributes('aria-describedby')).toContain('help-web.allowed_origins')
    expect(input.attributes('aria-invalid')).toBe('true')
  })
})

describe('SettingControl value changes', () => {
  it('emits update:modelValue on text input', async () => {
    const setting = value({ key: 'logging.file', type: 'string', widget: 'text' })
    const wrapper = mountControl(setting, 'old')
    await wrapper.find('[data-testid="setting-logging.file"]').setValue('new')
    expect(lastEmit(wrapper, 'update:modelValue')).toEqual(['new'])
  })

  it('emits update:modelValue on select change', async () => {
    const setting = value({
      key: 'logging.level',
      type: 'enum',
      widget: 'select',
      choices: ['INFO', 'DEBUG'],
    })
    const wrapper = mountControl(setting, 'INFO')
    await wrapper.find('[data-testid="setting-logging.level"]').setValue('DEBUG')
    expect(lastEmit(wrapper, 'update:modelValue')).toEqual(['DEBUG'])
  })
})

// Every widget branch the component can render. Shared by the cross-cutting
// suites below so a new branch has to be added here once and is then held to
// every rule, rather than being spot-checked against whichever it happened to
// be written alongside.
const BRANCHES: Array<{
  widget: string
  setting: Partial<SettingViewValue>
  modelValue: string | number | boolean | string[]
}> = [
    {
      widget: 'toggle',
      setting: { key: 'enrichment.enabled', type: 'bool', widget: 'toggle' },
      modelValue: true,
    },
    {
      widget: 'number (int)',
      setting: { key: 'enrichment.batch_size', type: 'int', widget: 'number' },
      modelValue: 25,
    },
    {
      widget: 'number (float)',
      setting: { key: 'recommendations.scorer_weights.genre_match', type: 'float', widget: 'number' },
      modelValue: 0.7,
    },
    {
      widget: 'text',
      setting: { key: 'logging.file', type: 'string', widget: 'text' },
      modelValue: 'logs/app.log',
    },
    {
      widget: 'select',
      setting: {
        key: 'logging.level',
        type: 'enum',
        widget: 'select',
        choices: ['INFO', 'DEBUG'],
      },
      modelValue: 'INFO',
    },
  {
    widget: 'tags',
    setting: { key: 'web.allowed_origins', type: 'list', widget: 'tags' },
    modelValue: ['http://localhost:5173'],
  },
]

describe('SettingControl locks every widget while a save is in flight', () => {
  // Enumerated, not spot-checked. Threading :disabled to most branches and
  // missing one is exactly the bug this covers: an unlocked control accepts an
  // edit mid-save that the in-flight response then silently overwrites.
  it.each(BRANCHES)('disables every focusable element in the $widget branch', (branch) => {
    // db_overridden renders the Reset button, whose `:disabled="disabled ||
    // resetting"` is otherwise absent from this sweep entirely — the point of
    // enumerating is that a missed control IS the bug.
    const wrapper = mountControl(
      value({ ...branch.setting, db_overridden: true }),
      branch.modelValue,
      { disabled: true },
    )
    const focusable = wrapper.findAll('input, button, select, textarea')
    expect(focusable.length).toBeGreaterThan(0)
    expect(wrapper.find(`[data-testid="reset-${branch.setting.key}"]`).exists()).toBe(
      true,
    )
    for (const element of focusable) {
      expect(element.attributes('disabled')).toBeDefined()
    }
  })
})

describe('SettingControl gives every widget an accessible name', () => {
  // A control with no programmatic name is announced as just its role ("edit
  // text, blank") — the label is on screen but never reaches the a11y tree.
  // Enumerated for the same reason as the lock above: the branches use three
  // different naming mechanisms (a <label for>, an atom's own <label>, and an
  // aria-label on the stepper input), so any one of them can regress alone.
  it.each(BRANCHES)('names the $widget branch control', (branch) => {
    const setting = value({ ...branch.setting, label: 'Human readable label' })
    const wrapper = mountControl(setting, branch.modelValue)

    // Assert the named element IS the control the section wires up, not merely
    // that *something* on screen is named. A `length > 0` check would let an
    // unnamed control pass in any branch that renders a second focusable
    // element (the tags branch renders two).
    //
    // Located by id, not data-testid: the atom-based branches forward the
    // testid to their root wrapper (a <label>, a <div>), while `inputId` is what
    // SettingsSection actually focuses and what aria-describedby points at.
    const inputId = `setting-${branch.setting.key}`
    const control = wrapper
      .findAll('input, select, textarea, button')
      .find((element) => element.attributes('id') === inputId)

    expect(control, `no focusable element carries id="${inputId}"`).toBeDefined()

    const labelled = wrapper
      .findAll('label')
      .some((label) => label.attributes('for') === inputId)

    expect(
      labelled || control!.attributes('aria-label') === 'Human readable label',
      `the ${branch.widget} control has no programmatic name`,
    ).toBe(true)
  })

  it.each(BRANCHES)('points every <label for> at an element that exists in the $widget branch', (branch) => {
    // A `for` naming a missing id is worse than no label: it looks correct in
    // review and in the DOM, and announces nothing.
    const wrapper = mountControl(value(branch.setting), branch.modelValue)

    // Compared against the rendered ids rather than via a `#id` selector: the
    // keys carry dots, which a CSS selector reads as class separators.
    const ids = wrapper
      .findAll('[id]')
      .map((element) => element.attributes('id'))

    for (const label of wrapper.findAll('label')) {
      const target = label.attributes('for')
      if (target === undefined) continue
      expect(ids, `<label for="${target}"> points at no element`).toContain(target)
    }
  })
})
