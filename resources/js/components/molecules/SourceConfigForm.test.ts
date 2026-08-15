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

  describe('fields the source never stored', () => {
    // Regression: every absent value was filled with the type's zero value, so
    // verify_ssl read false on a source that was verifying TLS — and onSave
    // wrote that fabrication back, storing it on the next unrelated save.
    const verifySsl = field({
      name: 'verify_ssl',
      field_type: 'bool',
      default: true,
    })

    it('renders an unset bool as its schema default, not as unchecked', () => {
      const wrapper = mount(SourceConfigForm, {
        props: { schema: [verifySsl], values: {}, secretStatus: {} },
      })

      const input = wrapper.find('input[name="verify_ssl"]')
      expect((input.element as HTMLInputElement).checked).toBe(true)
    })

    it('omits an untouched bool default when another field is saved', async () => {
      const wrapper = mount(SourceConfigForm, {
        props: {
          schema: [verifySsl, field({ name: 'url' })],
          values: { url: 'https://old' },
          secretStatus: {},
        },
      })

      await wrapper.find('input[name="url"]').setValue('https://new')
      await wrapper.find('[data-testid="form-save"]').trigger('click')

      expect(wrapper.emitted('save')![0][0]).toEqual({ url: 'https://new' })
    })

    it('saves a bool the user turned off against its default', async () => {
      const wrapper = mount(SourceConfigForm, {
        props: { schema: [verifySsl], values: {}, secretStatus: {} },
      })

      await wrapper.find('input[name="verify_ssl"]').setValue(false)
      await wrapper.find('[data-testid="form-save"]').trigger('click')

      expect(wrapper.emitted('save')![0][0]).toEqual({ verify_ssl: false })
    })
  })

  it('emits toggle-enabled with the inverted state on click', async () => {
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema: [field({ name: 'path' })],
        values: { path: 'x' },
        secretStatus: {},
        enabled: true,
      },
    })
    await wrapper.find('[data-testid="form-toggle-enabled"]').trigger('click')
    expect(wrapper.emitted('toggle-enabled')).toEqual([[false]])
  })

  it('renders an error pill with message when saveStatus is "error"', () => {
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema: [field({ name: 'path' })],
        values: { path: 'x' },
        secretStatus: {},
        saveStatus: 'error',
        saveError: 'boom',
      },
    })
    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.text()).toContain('boom')
    expect(status.attributes('role')).toBe('alert')
  })

  it('drops a second activation while saving instead of double-submitting', async () => {
    // aria-disabled does not block activation the way native disabled does, so
    // the guard in onSave is the only thing preventing a duplicate save.
    const wrapper = mount(SourceConfigForm, {
      props: {
        schema: [field({ name: 'path' })],
        values: { path: 'x' },
        secretStatus: {},
        saving: true,
      },
    })
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    expect(wrapper.emitted('save')).toBeUndefined()
  })
})
