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

// A plugin with TWO sensitive fields (mirrors Trakt's client_secret +
// refresh_token) so the ordering and per-field error-naming can be verified.
const twoSecretPlugin: PluginInfoResponse = {
  name: 'trakt_like',
  display_name: 'Trakt-like',
  description: 'Two secrets',
  content_types: ['tv'],
  requires_api_key: true,
  requires_network: true,
  fields: [
    {
      name: 'client_secret',
      field_type: 'str',
      required: true,
      default: null,
      description: '',
      sensitive: true,
    },
    {
      name: 'refresh_token',
      field_type: 'str',
      required: true,
      default: null,
      description: '',
      sensitive: true,
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
  }
}

async function mountWithPlugins(
  plugins: PluginInfoResponse[] = [calibrePlugin],
) {
  const wrapper = mount(AddSourceModal)
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

  it('prefills the Source id from the selected plugin name', async () => {
    const { wrapper } = await mountWithPlugins()
    const input = wrapper.find('#add-source-id')
      .element as HTMLInputElement
    expect(input.value).toBe('calibre_web')
  })

  it('updates the prefilled id when the plugin changes but not after the user edits it', async () => {
    const { wrapper } = await mountWithPlugins([calibrePlugin, filePlugin])
    const idInput = wrapper.find('#add-source-id')

    // Plugin change updates the (unedited) id.
    await wrapper.find('#add-source-plugin').setValue('my_books')
    expect((idInput.element as HTMLInputElement).value).toBe('my_books')

    // User edits the id — subsequent plugin changes must NOT clobber it.
    await idInput.setValue('custom-id')
    await wrapper.find('#add-source-plugin').setValue('calibre_web')
    expect((idInput.element as HTMLInputElement).value).toBe('custom-id')
  })

  it('accepts a hyphenated id as valid (no inline error)', async () => {
    const { wrapper } = await mountWithPlugins()
    await wrapper.find('#add-source-id').setValue('calibre-web')
    expect(wrapper.find('[data-testid="add-source-id-error"]').exists()).toBe(
      false,
    )
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

  it('renders a sensitive field as a password input', async () => {
    const { wrapper } = await mountWithPlugins()
    const secret = wrapper.find('[data-testid="add-source-secret-password"]')
    expect(secret.exists()).toBe(true)
    expect(secret.attributes('type')).toBe('password')
    expect(secret.attributes('autocomplete')).toBe('new-password')
  })

  it('disables Create with a visible reason when a required field is empty', async () => {
    const { wrapper } = await mountWithPlugins()
    // Required base_url, username, password all empty.
    const missing = wrapper.find('[data-testid="add-source-missing-fields"]')
    expect(missing.exists()).toBe(true)
    expect(missing.text()).toContain('password')
    expect(
      wrapper.find('[data-testid="add-source-submit"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('enables Create once every required field (including the secret) is filled', async () => {
    const { wrapper } = await mountWithPlugins()
    await wrapper.find('#add-source-field-base_url').setValue('http://cw')
    await wrapper.find('#add-source-field-username').setValue('me')
    await wrapper
      .find('[data-testid="add-source-secret-password"]')
      .setValue('hunter2')
    expect(
      wrapper.find('[data-testid="add-source-submit"]').attributes('disabled'),
    ).toBeUndefined()
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
    // Secret must NOT be in the create payload.
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

    // Source exists → list refreshed (created emitted) but modal stays open.
    expect(wrapper.emitted('created')).toEqual([['calibre-web']])
    expect(wrapper.emitted('close')).toBeFalsy()
    const alert = wrapper.find('.add-source-error')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('was created')
    // Names the actual failing field (this plugin's secret is "password").
    expect(alert.text()).toContain('password')
  })

  it('sets each secret once in field order for a two-secret plugin', async () => {
    const { wrapper, store } = await mountWithPlugins([twoSecretPlugin])
    vi.spyOn(store, 'createSource').mockResolvedValue(
      createdConfig('trakt-like', 'trakt_like'),
    )
    const setSecret = vi
      .spyOn(store, 'setSourceSecret')
      .mockResolvedValue(undefined)

    await wrapper.find('#add-source-id').setValue('trakt-like')
    await wrapper
      .find('[data-testid="add-source-secret-client_secret"]')
      .setValue('cs')
    await wrapper
      .find('[data-testid="add-source-secret-refresh_token"]')
      .setValue('rt')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    expect(setSecret).toHaveBeenCalledTimes(2)
    expect(setSecret.mock.calls[0]).toEqual(['trakt-like', 'client_secret', 'cs'])
    expect(setSecret.mock.calls[1]).toEqual([
      'trakt-like',
      'refresh_token',
      'rt',
    ])
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('names the SECOND secret in the error when it is the one that fails', async () => {
    const { wrapper, store } = await mountWithPlugins([twoSecretPlugin])
    vi.spyOn(store, 'createSource').mockResolvedValue(
      createdConfig('trakt-like', 'trakt_like'),
    )
    // First secret saves, second rejects.
    vi.spyOn(store, 'setSourceSecret')
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('boom'))

    await wrapper.find('#add-source-id').setValue('trakt-like')
    await wrapper
      .find('[data-testid="add-source-secret-client_secret"]')
      .setValue('cs')
    await wrapper
      .find('[data-testid="add-source-secret-refresh_token"]')
      .setValue('rt')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    const alert = wrapper.find('.add-source-error')
    expect(alert.text()).toContain('refresh_token')
    // Must NOT mislabel it as the first field or a generic "password".
    expect(alert.text()).not.toContain('password')
    expect(wrapper.emitted('close')).toBeFalsy()
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
    // Leave the optional "token" secret empty.
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    expect(setSecret).not.toHaveBeenCalled()
    expect(wrapper.emitted('created')).toEqual([['opt']])
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('re-enables the Create button after the partial-failure path', async () => {
    const { wrapper, store } = await mountWithPlugins()
    vi.spyOn(store, 'createSource').mockResolvedValue(
      createdConfig('calibre-web'),
    )
    vi.spyOn(store, 'setSourceSecret').mockRejectedValue(new Error('down'))

    await wrapper.find('#add-source-id').setValue('calibre-web')
    await wrapper.find('#add-source-field-base_url').setValue('http://cw')
    await wrapper.find('#add-source-field-username').setValue('me')
    await wrapper
      .find('[data-testid="add-source-secret-password"]')
      .setValue('hunter2')
    await wrapper.find('[data-testid="add-source-submit"]').trigger('click')
    await flushPromises()

    // submitting reset → button is interactive again so the user can retry.
    expect(
      wrapper.find('[data-testid="add-source-submit"]').attributes('disabled'),
    ).toBeUndefined()
  })
})
