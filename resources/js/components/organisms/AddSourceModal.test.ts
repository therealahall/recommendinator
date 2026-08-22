import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import AddSourceModal from './AddSourceModal.vue'
import { useDataStore } from '@/stores/data'
import type { PluginInfoResponse, SourceConfigResponse } from '@/types/api'

const calibrePlugin: PluginInfoResponse = {
  name: 'calibre_web',
  display_name: 'Calibre-Web',
  description: 'Sync a Calibre-Web library',
  content_types: ['book'],
  requires_api_key: false,
  requires_network: true,
  fields: [
    {
      name: 'base_url',
      field_type: 'str',
      required: true,
      default: '',
      description: '',
      sensitive: false,
    },
    {
      name: 'username',
      field_type: 'str',
      required: true,
      default: '',
      description: '',
      sensitive: false,
    },
    {
      name: 'password',
      field_type: 'str',
      required: true,
      default: null,
      description: '',
      sensitive: true,
    },
  ],
}

const filePlugin: PluginInfoResponse = {
  name: 'my_books',
  display_name: 'Book CSV',
  description: 'Import from a CSV',
  content_types: ['book'],
  requires_api_key: false,
  requires_network: false,
  fields: [
    {
      name: 'path',
      field_type: 'str',
      required: true,
      default: '',
      description: '',
      sensitive: false,
    },
  ],
}

// A plugin with a required non-sensitive field and an OPTIONAL secret, so the
// "empty optional secret is skipped" path can be exercised.
const optionalSecretPlugin: PluginInfoResponse = {
  name: 'opt_secret',
  display_name: 'Optional Secret',
  description: 'Optional secret',
  content_types: ['book'],
  requires_api_key: false,
  requires_network: true,
  fields: [
    {
      name: 'base_url',
      field_type: 'str',
      required: true,
      default: '',
      description: '',
      sensitive: false,
    },
    {
      name: 'token',
      field_type: 'str',
      required: false,
      default: null,
      description: '',
      sensitive: true,
    },
  ],
}

function createdConfig(sourceId: string, plugin = 'calibre_web'): SourceConfigResponse {
  return {
    source_id: sourceId,
    plugin,
    plugin_display_name: 'Calibre-Web',
    enabled: true,
    migrated: true,
    migrated_at: '2026-07-22T00:00:00Z',
    field_values: {},
    secret_status: {},
    sync_interval: 'daily',
  }
}

async function mountWithPlugins(
  plugins: PluginInfoResponse[] = [calibrePlugin],
  attachTo?: HTMLElement,
) {
  const wrapper = mount(AddSourceModal, { attachTo })
  const store = useDataStore()
  store.availablePlugins = plugins
  vi.spyOn(store, 'loadAvailablePlugins').mockResolvedValue(plugins)
  vi.spyOn(store, 'loadSyncSources').mockResolvedValue(undefined)
  await flushPromises()
  return { wrapper, store }
}

describe('AddSourceModal', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('updates the prefilled id when the plugin changes but not after the user edits it', async () => {
    const { wrapper } = await mountWithPlugins([calibrePlugin, filePlugin])
    const idInput = wrapper.find('#add-source-id')

    await wrapper.find('#add-source-plugin').setValue('my_books')
    expect((idInput.element as HTMLInputElement).value).toBe('my_books')

    await idInput.setValue('custom-id')
    await wrapper.find('#add-source-plugin').setValue('calibre_web')
    expect((idInput.element as HTMLInputElement).value).toBe('custom-id')
  })

  it('asks before a backdrop click discards a typed API key, and declining keeps it and the focus', async () => {
    const { wrapper } = await mountWithPlugins([calibrePlugin], document.body)

    await wrapper.trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()

    await wrapper.find('[data-testid="add-source-secret-password"]').setValue('hunter2')
    // A real backdrop press blurs to <body> before the click, which jsdom skips.
    ;(document.activeElement as HTMLElement).blur()
    await wrapper.trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    const asked = wrapper.get('[role="alertdialog"]')

    await asked.findAll('button').find((b) => b.text() === 'Keep editing')!.trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(
      (wrapper.find('[data-testid="add-source-secret-password"]').element as HTMLInputElement)
        .value,
    ).toBe('hunter2')
    // Declining left focus on <body>, so the next Tab walked out of the dialog.
    expect(wrapper.get('[aria-modal="true"]').element.contains(document.activeElement)).toBe(true)
    wrapper.unmount()
  })

  it('shows an inline error and disables Create for an invalid id', async () => {
    const { wrapper } = await mountWithPlugins()
    await wrapper.find('#add-source-id').setValue('Bad ID')
    const error = wrapper.find('[data-testid="add-source-id-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('lowercase')
    expect(
      wrapper.find('[data-testid="add-source-submit"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('calls createSource without the secret, then setSourceSecret for it', async () => {
    const { wrapper, store } = await mountWithPlugins()
    const create = vi
      .spyOn(store, 'createSource')
      .mockResolvedValue(createdConfig('calibre-web'))
    const setSecret = vi
      .spyOn(store, 'setSourceSecret')
      .mockResolvedValue(undefined)

    await wrapper.find('#add-source-id').setValue('calibre-web')
    await wrapper.find('#add-source-field-base_url').setValue('http://cw')
    await wrapper.find('#add-source-field-username').setValue('me')
    await wrapper
      .find('[data-testid="add-source-secret-password"]')
      .setValue('hunter2')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledTimes(1)
    const payload = create.mock.calls[0][0]
    expect(payload.id).toBe('calibre-web')
    expect(payload.values).not.toHaveProperty('password')
    expect(payload.values).toMatchObject({
      base_url: 'http://cw',
      username: 'me',
    })
    expect(setSecret).toHaveBeenCalledWith('calibre-web', 'password', 'hunter2')
    expect(wrapper.emitted('created')).toEqual([['calibre-web']])
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('surfaces a partial-failure error without closing when setSourceSecret rejects', async () => {
    const { wrapper, store } = await mountWithPlugins()
    vi.spyOn(store, 'createSource').mockResolvedValue(
      createdConfig('calibre-web'),
    )
    vi.spyOn(store, 'setSourceSecret').mockRejectedValue(
      new Error('secret store down'),
    )

    await wrapper.find('#add-source-id').setValue('calibre-web')
    await wrapper.find('#add-source-field-base_url').setValue('http://cw')
    await wrapper.find('#add-source-field-username').setValue('me')
    await wrapper
      .find('[data-testid="add-source-secret-password"]')
      .setValue('hunter2')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('created')).toEqual([['calibre-web']])
    expect(wrapper.emitted('close')).toBeFalsy()
    const alert = wrapper.find('.add-source-error')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('was created')
    expect(alert.text()).toContain('password')
  })

  it('skips setSourceSecret for an empty optional secret while still creating', async () => {
    const { wrapper, store } = await mountWithPlugins([optionalSecretPlugin])
    vi.spyOn(store, 'createSource').mockResolvedValue(
      createdConfig('opt', 'opt_secret'),
    )
    const setSecret = vi
      .spyOn(store, 'setSourceSecret')
      .mockResolvedValue(undefined)

    await wrapper.find('#add-source-id').setValue('opt')
    await wrapper.find('#add-source-field-base_url').setValue('http://x')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    expect(setSecret).not.toHaveBeenCalled()
    expect(wrapper.emitted('created')).toEqual([['opt']])
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  describe('while a create is in flight', () => {
    const FOCUSABLE =
      'button:not([disabled]), input:not([disabled]), select:not([disabled]),' +
      ' textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

    async function mountMidSubmit() {
      const { wrapper, store } = await mountWithPlugins()
      vi.spyOn(store, 'createSource').mockReturnValue(new Promise(() => {}))
      await wrapper.find('#add-source-id').setValue('calibre-web')
      await wrapper.find('#add-source-field-base_url').setValue('http://cw')
      await wrapper.find('#add-source-field-username').setValue('me')
      await wrapper
        .find('[data-testid="add-source-secret-password"]')
        .setValue('hunter2')
      await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('keeps the submit button focusable so the trap holds', async () => {
      // `disabled` everywhere left none, so Tab walked out (WCAG 2.4.3).
      const { wrapper, store } = await mountMidSubmit()

      expect(store.createSource).toHaveBeenCalled()
      expect(
        wrapper.find('[data-testid="add-source-submit"]').element.matches(FOCUSABLE),
      ).toBe(true)
    })
  })
})
