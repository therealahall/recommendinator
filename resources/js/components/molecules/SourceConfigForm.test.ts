import { describe, it, expect } from 'vitest'
import { mount, enableAutoUnmount } from '@vue/test-utils'
import { afterEach } from 'vitest'
import SourceConfigForm from './SourceConfigForm.vue'
import type { SourceFieldSchema } from '@/types/api'

enableAutoUnmount(afterEach)

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

type FormProps = InstanceType<typeof SourceConfigForm>['$props']

function mountForm(props: Partial<FormProps> & Pick<FormProps, 'schema'>) {
  return mount(SourceConfigForm, {
    props: { values: {}, secretStatus: {}, sourceName: 'Calibre', ...props },
    attachTo: document.body,
  })
}

describe('SourceConfigForm', () => {
  it('emits set-secret with field name and value on Save secret', async () => {
    const wrapper = mountForm({
      schema: [field({ name: 'api_key', sensitive: true })],
      secretStatus: { api_key: false },
    })

    await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
    await wrapper.find('input[name="api_key"]').setValue('rotated')
    await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')

    expect(wrapper.emitted('set-secret')).toEqual([['api_key', 'rotated']])
  })

  it('emits save with the merged values on Save click', async () => {
    const wrapper = mountForm({
      schema: [field({ name: 'path' }), field({ name: 'min_minutes', field_type: 'int' })],
      values: { path: '/old', min_minutes: 0 },
    })

    await wrapper.find('input[name="path"]').setValue('/new')
    await wrapper.find('input[name="min_minutes"]').setValue('60')
    await wrapper.find('[data-testid="form-save"]').trigger('click')

    const saved = wrapper.emitted('save')
    expect(saved).toHaveLength(1)
    expect(saved![0][0]).toEqual({ path: '/new', min_minutes: 60 })
  })

  it('emits save including the edited list value', async () => {
    const wrapper = mountForm({
      schema: [field({ name: 'tags', field_type: 'list' })],
      values: { tags: ['rpg'] },
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
      const wrapper = mountForm({ schema: [verifySsl] })

      const input = wrapper.find('input[name="verify_ssl"]')
      expect((input.element as HTMLInputElement).checked).toBe(true)
    })

    it('omits an untouched bool default when another field is saved', async () => {
      const wrapper = mountForm({
        schema: [verifySsl, field({ name: 'url' })],
        values: { url: 'https://old' },
      })

      await wrapper.find('input[name="url"]').setValue('https://new')
      await wrapper.find('[data-testid="form-save"]').trigger('click')

      expect(wrapper.emitted('save')![0][0]).toEqual({ url: 'https://new' })
    })

    it('saves a bool the user turned off against its default', async () => {
      const wrapper = mountForm({ schema: [verifySsl] })

      await wrapper.find('input[name="verify_ssl"]').setValue(false)
      await wrapper.find('[data-testid="form-save"]').trigger('click')

      expect(wrapper.emitted('save')![0][0]).toEqual({ verify_ssl: false })
    })
  })

  it('emits toggle-enabled with the inverted state on click', async () => {
    const wrapper = mountForm({
      schema: [field({ name: 'path' })],
      values: { path: 'x' },
      enabled: true,
    })
    await wrapper.find('[data-testid="form-toggle-enabled"]').trigger('click')
    expect(wrapper.emitted('toggle-enabled')).toEqual([[false]])
  })

  it('keeps Enable in the tab order while its request is in flight, and drops the second press', async () => {
    const wrapper = mountForm({
      schema: [field({ name: 'path' })],
      values: { path: 'x' },
      enabled: false,
    })
    const button = wrapper.get('[data-testid="form-toggle-enabled"]')
    const element = button.element as HTMLButtonElement
    element.focus()

    await button.trigger('click')
    await wrapper.setProps({ enableBusy: true })
    await button.trigger('click')

    expect(wrapper.emitted('toggle-enabled')).toEqual([[true]])
    expect(document.activeElement).toBe(element)
    element.blur()
    element.focus()
    expect(document.activeElement).toBe(element)
  })

  it('keeps the fields reachable when a scheduled sync starts mid-edit', async () => {
    const wrapper = mountForm({ schema: [field({ name: 'path' })], values: { path: 'x' } })
    const input = wrapper.get('input[name="path"]').element as HTMLInputElement
    input.focus()

    await wrapper.setProps({ disabled: true })

    expect(document.activeElement).toBe(input)
    input.blur()
    input.focus()
    expect(document.activeElement).toBe(input)
  })

  it('renders an error pill with message when saveStatus is "error"', () => {
    const wrapper = mountForm({
      schema: [field({ name: 'path' })],
      values: { path: 'x' },
      saveStatus: 'error',
      saveError: 'boom',
    })
    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.text()).toContain('boom')
    expect(status.attributes('role')).toBe('alert')
  })

  it('drops a second activation while saving instead of double-submitting', async () => {
    // aria-disabled does not block activation, so onSave's guard is the only stop.
    const wrapper = mountForm({
      schema: [field({ name: 'path' })],
      values: { path: 'x' },
      saving: true,
    })
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    expect(wrapper.emitted('save')).toBeUndefined()
  })

  describe('clearing a secret', () => {
    const apiKey = [field({ name: 'api_key', sensitive: true })]

    it('asks before destroying the credential instead of clearing on the click', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: true } })

      await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')

      expect(wrapper.emitted('clear-secret')).toBeUndefined()
      expect(wrapper.get('[data-testid="confirm-panel"]').text()).toContain('api_key')
      expect(wrapper.get('[data-testid="confirm-panel"]').text()).toContain('Calibre')
    })

    it('emits clear-secret once the question is answered', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: true } })

      await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')

      expect(wrapper.emitted('clear-secret')).toEqual([['api_key']])
    })

    it('keeps the credential and the keyboard on Clear when the question is declined', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: true } })

      await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-cancel"]').trigger('click')
      await wrapper.vm.$nextTick()

      expect(wrapper.emitted('clear-secret')).toBeUndefined()
      expect(document.activeElement).toBe(
        wrapper.get('[data-testid="secret-clear-api_key"]').element,
      )
    })
  })

  describe('storing a secret', () => {
    const apiKey = [field({ name: 'api_key', sensitive: true })]

    async function typeSecret(wrapper: ReturnType<typeof mountForm>) {
      await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
      await wrapper.find('input[name="api_key"]').setValue('rotated')
      await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')
    }

    it('holds the edit row open until the request answers', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: false } })

      await typeSecret(wrapper)
      await wrapper.setProps({ secretSave: { api_key: 'saving' } })

      expect(wrapper.find('input[name="api_key"]').exists()).toBe(true)
    })

    it('reports a refused store in the row and puts focus on it', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: false } })

      await typeSecret(wrapper)
      await wrapper.setProps({
        secretSave: { api_key: 'error' },
        secretSaveError: { api_key: 'Key rejected' },
      })
      await wrapper.vm.$nextTick()

      const alert = wrapper.get('[data-testid="secret-error-api_key"]')
      expect(alert.text()).toContain('Key rejected')
      expect(wrapper.find('input[name="api_key"]').exists()).toBe(true)
      expect(document.activeElement).toBe(alert.element)
    })

    it('confirms a stored key only once the request resolved, and closes the row', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: false } })

      await typeSecret(wrapper)
      await wrapper.setProps({
        secretSave: { api_key: 'saved' },
        secretStatus: { api_key: true },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.get('[data-testid="secret-saved-api_key"]').text()).toBe(
        'api_key saved',
      )
      expect(wrapper.find('input[name="api_key"]').exists()).toBe(false)
      expect(document.activeElement).toBe(
        wrapper.get('[data-testid="secret-replace-api_key"]').element,
      )
    })

    it('says the key was cleared, not saved, when the round trip left it unset', async () => {
      const wrapper = mountForm({ schema: apiKey, secretStatus: { api_key: true } })

      await wrapper.setProps({
        secretSave: { api_key: 'saved' },
        secretStatus: { api_key: false },
      })

      expect(wrapper.get('[data-testid="secret-saved-api_key"]').text()).toBe(
        'api_key cleared',
      )
    })
  })

  it('names the field in every secret control, so two secrets are told apart', async () => {
    const wrapper = mountForm({
      schema: [
        field({ name: 'client_id', sensitive: true }),
        field({ name: 'client_secret', sensitive: true }),
      ],
      secretStatus: { client_id: true, client_secret: true },
    })

    await wrapper.find('[data-testid="secret-replace-client_secret"]').trigger('click')

    const names = wrapper
      .findAll('fieldset.source-form-secrets button')
      .map((button) => button.attributes('aria-label'))

    expect(names).toEqual([
      'Replace client_id',
      'Clear client_id',
      'Save client_secret',
      'Cancel replacing client_secret',
    ])
    expect(new Set(names).size).toBe(names.length)
  })

  it('ties the set/unset badge to the field it describes', () => {
    const wrapper = mountForm({
      schema: [field({ name: 'api_key', sensitive: true })],
      secretStatus: { api_key: true },
    })

    expect(wrapper.get('.secret-status-badge').text()).toBe('api_key secret is set')
  })
})
