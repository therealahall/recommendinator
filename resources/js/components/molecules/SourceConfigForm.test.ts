import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SourceConfigForm from './SourceConfigForm.vue'
import type { SourceFieldSchema } from '@/types/api'

function field(overrides: Partial<SourceFieldSchema>): SourceFieldSchema {
  return {
    name: 'name',
    field_type: 'str',
    required: false,
    default: null,
    description: '',
    sensitive: false,
    ...overrides,
  }
}

describe('SourceConfigForm', () => {
  it('renders a text input for str fields', () => {
    const schema: SourceFieldSchema[] = [field({ name: 'path' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { path: '/data/x.csv' },
        secretStatus: {},
      },
    })

    const input = wrapper.find('input[name="path"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('type')).toBe('text')
    expect((input.element as HTMLInputElement).value).toBe('/data/x.csv')
  })

  it('renders a number input for int fields', () => {
    const schema = [field({ name: 'min_minutes', field_type: 'int' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { min_minutes: 30 },
        secretStatus: {},
      },
    })

    const input = wrapper.find('input[name="min_minutes"]')
    expect(input.attributes('type')).toBe('number')
  })

  it('renders a number input with step="any" for float fields', () => {
    const schema = [field({ name: 'rate', field_type: 'float' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { rate: 0.5 },
        secretStatus: {},
      },
    })

    const input = wrapper.find('input[name="rate"]')
    expect(input.attributes('type')).toBe('number')
    expect(input.attributes('step')).toBe('any')
  })

  it('renders an empty list field with no chips and a draft input', () => {
    const schema = [field({ name: 'tags', field_type: 'list' })]
    const wrapper = mount(SourceConfigForm, {
      props: { schema, values: { tags: [] }, secretStatus: {} },
    })

    expect(wrapper.findAll('[data-testid="chip"]')).toHaveLength(0)
    expect(wrapper.find('[data-testid="chip-input-tags"]').exists()).toBe(true)
  })

  it('renders a checkbox for bool fields', () => {
    const schema = [field({ name: 'enabled_filter', field_type: 'bool' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { enabled_filter: true },
        secretStatus: {},
      },
    })

    const input = wrapper.find('input[name="enabled_filter"]')
    expect(input.attributes('type')).toBe('checkbox')
    expect((input.element as HTMLInputElement).checked).toBe(true)
  })

  it('renders chips for list fields', () => {
    const schema = [field({ name: 'tags', field_type: 'list' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { tags: ['rpg', 'indie'] },
        secretStatus: {},
      },
    })

    const chips = wrapper.findAll('[data-testid="chip"]')
    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('rpg')
    expect(chips[1].text()).toContain('indie')
  })

  it('marks required text fields with required attribute', () => {
    const schema = [field({ name: 'path', required: true })]
    const wrapper = mount(SourceConfigForm, {
      props: { schema, values: { path: 'x' }, secretStatus: {} },
    })

    expect(
      wrapper.find('input[name="path"]').attributes('required'),
    ).toBeDefined()
  })

  it('shows description text for fields when provided', () => {
    const schema = [
      field({ name: 'path', description: 'Path to the data file' }),
    ]
    const wrapper = mount(SourceConfigForm, {
      props: { schema, values: { path: 'x' }, secretStatus: {} },
    })

    expect(wrapper.text()).toContain('Path to the data file')
  })

  it('renders a sensitive field as a "set" badge with Replace + Clear buttons', () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: true },
      },
    })

    expect(wrapper.text()).toContain('set')
    expect(wrapper.find('[data-testid="secret-replace-api_key"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="secret-clear-api_key"]').exists()).toBe(
      true,
    )
    // Plain text input is hidden until Replace is clicked
    expect(wrapper.find('input[name="api_key"]').exists()).toBe(false)
  })

  it('shows password input after clicking Replace on a sensitive field', async () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: true },
      },
    })

    await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
    const input = wrapper.find('input[name="api_key"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('type')).toBe('password')
  })

  it('emits set-secret with field name and value on Save secret', async () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: false },
      },
    })

    await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
    await wrapper.find('input[name="api_key"]').setValue('rotated')
    await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')

    expect(wrapper.emitted('set-secret')).toEqual([['api_key', 'rotated']])
  })

  it('cancelling secret edit closes the input without emitting set-secret', async () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: false },
      },
    })

    await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
    await wrapper.find('input[name="api_key"]').setValue('partial')
    await wrapper.find('[data-testid="secret-cancel-api_key"]').trigger('click')

    expect(wrapper.emitted('set-secret')).toBeUndefined()
    expect(wrapper.find('input[name="api_key"]').exists()).toBe(false)
  })

  it('saving an empty secret draft is a no-op (does not emit set-secret)', async () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: true },
      },
    })

    await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
    // Leave input blank, click Save
    await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')

    expect(wrapper.emitted('set-secret')).toBeUndefined()
  })

  it('emits clear-secret with field name on Clear', async () => {
    const schema = [field({ name: 'api_key', sensitive: true })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: {},
        secretStatus: { api_key: true },
      },
    })

    await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')
    expect(wrapper.emitted('clear-secret')).toEqual([['api_key']])
  })

  it('emits save with the merged values on Save click', async () => {
    const schema: SourceFieldSchema[] = [
      field({ name: 'path' }),
      field({ name: 'min_minutes', field_type: 'int' }),
    ]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { path: '/old', min_minutes: 0 },
        secretStatus: {},
      },
    })

    await wrapper.find('input[name="path"]').setValue('/new')
    await wrapper.find('input[name="min_minutes"]').setValue('60')
    await wrapper.find('[data-testid="form-save"]').trigger('click')

    const saved = wrapper.emitted('save')
    expect(saved).toHaveLength(1)
    expect(saved![0][0]).toEqual({ path: '/new', min_minutes: 60 })
  })

  it('adds a chip when typing and pressing Enter', async () => {
    const schema = [field({ name: 'tags', field_type: 'list' })]
    const wrapper = mount(SourceConfigForm, {
      props: { schema, values: { tags: ['rpg'] }, secretStatus: {} },
    })

    const input = wrapper.find('input[data-testid="chip-input-tags"]')
    await input.setValue('indie')
    await input.trigger('keydown.enter')

    const chips = wrapper.findAll('[data-testid="chip"]')
    expect(chips).toHaveLength(2)
    expect(chips[1].text()).toContain('indie')
  })

  it('removes a chip on its remove button click', async () => {
    const schema = [field({ name: 'tags', field_type: 'list' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { tags: ['rpg', 'indie'] },
        secretStatus: {},
      },
    })

    await wrapper
      .find('[data-testid="chip-remove-tags-0"]')
      .trigger('click')
    const chips = wrapper.findAll('[data-testid="chip"]')
    expect(chips).toHaveLength(1)
    expect(chips[0].text()).toContain('indie')
  })

  it('emits save including the edited list value', async () => {
    const schema = [field({ name: 'tags', field_type: 'list' })]
    const wrapper = mount(SourceConfigForm, {
      props: { schema, values: { tags: ['rpg'] }, secretStatus: {} },
    })

    const input = wrapper.find('input[data-testid="chip-input-tags"]')
    await input.setValue('indie')
    await input.trigger('keydown.enter')
    await wrapper.find('[data-testid="form-save"]').trigger('click')

    const saved = wrapper.emitted('save')
    expect(saved).toHaveLength(1)
    expect(saved![0][0]).toEqual({ tags: ['rpg', 'indie'] })
  })

  it('disables the Save button while saving', () => {
    const schema = [field({ name: 'path' })]
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema,
        values: { path: 'x' },
        secretStatus: {},
        saving: true,
      },
    })

    expect(
      wrapper.find('[data-testid="form-save"]').attributes('disabled'),
    ).toBeDefined()
  })
})
