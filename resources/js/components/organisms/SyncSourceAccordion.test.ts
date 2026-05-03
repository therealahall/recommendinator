import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SyncSourceAccordion from './SyncSourceAccordion.vue'
import { useDataStore } from '@/stores/data'
import type { SourceConfigResponse, SourceSchemaResponse } from '@/types/api'

const baseSource = {
  id: 'steam',
  display_name: 'Steam',
  plugin_display_name: 'Steam',
  enabled: true,
}

const disabledSource = {
  ...baseSource,
  enabled: false,
}

const baseSchema: SourceSchemaResponse = {
  source_id: 'steam',
  plugin: 'steam',
  plugin_display_name: 'Steam',
  fields: [
    {
      name: 'vanity_url',
      field_type: 'str',
      required: false,
      default: '',
      description: '',
      sensitive: false,
    },
    {
      name: 'api_key',
      field_type: 'str',
      required: true,
      default: null,
      description: '',
      sensitive: true,
    },
  ],
}

const migratedConfig: SourceConfigResponse = {
  source_id: 'steam',
  plugin: 'steam',
  plugin_display_name: 'Steam',
  enabled: true,
  migrated: true,
  migrated_at: '2026-05-03T00:00:00Z',
  field_values: { vanity_url: 'me' },
  secret_status: { api_key: true },
}

const yamlConfig: SourceConfigResponse = {
  ...migratedConfig,
  migrated: false,
  migrated_at: null,
}

describe('SyncSourceAccordion', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows only the source name and Sync button when collapsed', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    await flushPromises()

    // Trigger button shows display name
    const trigger = wrapper.find('button.accordion-trigger')
    expect(trigger.text()).toContain('Steam')

    // Sync button is rendered, sibling to the trigger
    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.exists()).toBe(true)
    expect(trigger.element.contains(sync.element)).toBe(false)

    // Disconnect button never appears in the collapsed view
    expect(wrapper.find('[data-testid="disconnect-btn-steam"]').exists()).toBe(
      false,
    )
  })

  it('emits sync with the source id when the Sync button is clicked', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    await flushPromises()

    await wrapper.find('[data-testid="sync-btn-steam"]').trigger('click')
    expect(wrapper.emitted('sync')).toEqual([['steam']])
  })

  function primeStore(
    store: ReturnType<typeof useDataStore>,
    cfg: SourceConfigResponse,
  ) {
    const loadSchema = vi
      .spyOn(store, 'loadSourceSchema')
      .mockImplementation(async (id: string) => {
        store.sourceSchemas[id] = baseSchema
        return baseSchema
      })
    const loadConfig = vi
      .spyOn(store, 'loadSourceConfig')
      .mockImplementation(async (id: string) => {
        store.sourceConfigs[id] = cfg
        return cfg
      })
    return { loadSchema, loadConfig }
  }

  it('clicking the trigger loads schema and config and expands the panel', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    const { loadSchema, loadConfig } = primeStore(store, yamlConfig)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()

    expect(loadSchema).toHaveBeenCalledWith('steam')
    expect(loadConfig).toHaveBeenCalledWith('steam')
    expect(
      wrapper.find('button.accordion-trigger').attributes('aria-expanded'),
    ).toBe('true')
  })

  it('shows the Migrate to DB button when the source is not yet migrated', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, yamlConfig)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="migrate-btn-steam"]').exists()).toBe(true)
    // Pre-migration: form is not rendered
    expect(wrapper.find('[data-testid="form-save"]').exists()).toBe(false)
  })

  it('shows the config form and enabled toggle once migrated', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="form-save"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="form-toggle-enabled"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="migrate-btn-steam"]').exists()).toBe(
      false,
    )
  })

  it('clicking Migrate calls store.migrateSource', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, yamlConfig)
    const migrate = vi.spyOn(store, 'migrateSource').mockResolvedValue({
      source_id: 'steam',
      migrated_at: 'now',
      fields_migrated: [],
      secrets_migrated: [],
    })

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="migrate-btn-steam"]').trigger('click')
    await flushPromises()

    expect(migrate).toHaveBeenCalledWith('steam')
  })

  it('renders the Sync button label as Syncing… while syncing', () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: true, disabled: false },
    })

    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.text()).toBe('Syncing…')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('aria-label')).toContain('Syncing Steam')
  })

  it('disables the Sync button when another sync is running (disabled prop)', () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: true },
    })

    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('aria-label')).toContain('another sync is in progress')
  })

  it('disables the Sync button and shows a Disabled badge when source.enabled is false', () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: disabledSource, syncing: false, disabled: false },
    })

    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('aria-label')).toContain('source is disabled')
    expect(wrapper.text()).toContain('Disabled')
  })

  it('does not emit sync when disabled source button is clicked', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: disabledSource, syncing: false, disabled: false },
    })

    await wrapper.find('[data-testid="sync-btn-steam"]').trigger('click')
    expect(wrapper.emitted('sync')).toBeUndefined()
  })

  it('clicking the Disable button on an enabled source calls store.setSourceEnabled(false)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    const setEnabled = vi
      .spyOn(store, 'setSourceEnabled')
      .mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper
      .find('[data-testid="form-toggle-enabled"]')
      .trigger('click')

    expect(setEnabled).toHaveBeenCalledWith('steam', false)
  })

  it('clicking the Enable button on a disabled source calls store.setSourceEnabled(true)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, { ...migratedConfig, enabled: false })
    const setEnabled = vi
      .spyOn(store, 'setSourceEnabled')
      .mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper
      .find('[data-testid="form-toggle-enabled"]')
      .trigger('click')

    expect(setEnabled).toHaveBeenCalledWith('steam', true)
  })

  it('saving the form forwards the values to store.updateSourceConfig', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    const update = vi
      .spyOn(store, 'updateSourceConfig')
      .mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()

    // Type into the path field, then click Save.
    await wrapper.find('input[name="vanity_url"]').setValue('updated')
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledTimes(1)
    expect(update.mock.calls[0][0]).toBe('steam')
    expect(update.mock.calls[0][1]).toMatchObject({ vanity_url: 'updated' })
  })

  it('clicking Remove with confirm=true calls store.deleteSource', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    const remove = vi.spyOn(store, 'deleteSource').mockResolvedValue(undefined)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="remove-btn-steam"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(remove).toHaveBeenCalledWith('steam')
    confirmSpy.mockRestore()
  })

  it('clicking Remove with confirm=false does NOT call store.deleteSource', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    const remove = vi.spyOn(store, 'deleteSource').mockResolvedValue(undefined)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="remove-btn-steam"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(remove).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('renders the Saved status pill after a successful save', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    vi.spyOn(store, 'updateSourceConfig').mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('input[name="vanity_url"]').setValue('renamed')
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.exists()).toBe(true)
    expect(status.text()).toContain('Saved')
  })

  it('renders the Error status pill when updateSourceConfig rejects', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    vi.spyOn(store, 'updateSourceConfig').mockRejectedValue(
      new Error('save blew up'),
    )

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.exists()).toBe(true)
    expect(status.text()).toContain('save blew up')
    expect(status.attributes('role')).toBe('alert')
  })

  it('disables the toggle button while setSourceEnabled is in flight (re-entrant guard)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false, disabled: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    // Hold the toggle in-flight so the second click hits the busy guard.
    let releaseToggle: () => void = () => {}
    const inflight = new Promise<void>((resolve) => {
      releaseToggle = resolve
    })
    const setEnabled = vi
      .spyOn(store, 'setSourceEnabled')
      .mockImplementation(() => inflight)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    const toggle = wrapper.find('[data-testid="form-toggle-enabled"]')
    await toggle.trigger('click')
    // Second click while the first is still pending must be a no-op.
    await toggle.trigger('click')
    expect(setEnabled).toHaveBeenCalledTimes(1)
    // Release the in-flight call so component teardown isn't fighting timers.
    releaseToggle()
    await flushPromises()
  })
})
