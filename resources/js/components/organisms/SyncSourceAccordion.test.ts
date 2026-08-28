import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SyncSourceAccordion from './SyncSourceAccordion.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import { useDataStore, type OAuthStatus } from '@/stores/data'
import type {
  SourceConfigResponse,
  SourceSchemaResponse,
  SyncErrorResponse,
  SyncSourceProgressResponse,
} from '@/types/api'

const TWO_HOURS_AGO = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
const IN_SIX_HOURS = new Date(Date.now() + 6 * 60 * 60 * 1000).toISOString()

const baseSource = {
  id: 'steam',
  display_name: 'Steam',
  plugin_display_name: 'Steam',
  enabled: true,
  plugin_not_loaded: null,
  sync_interval: '6h',
  last_run_at: TWO_HOURS_AGO,
  last_run_status: 'completed',
  next_run_at: IN_SIX_HOURS,
}

const neverSyncedSource = {
  ...baseSource,
  last_run_at: null,
  last_run_status: null,
  next_run_at: new Date().toISOString(),
}

const unscheduledSource = {
  ...baseSource,
  sync_interval: 'off',
  next_run_at: null,
}

const disabledSource = {
  ...baseSource,
  enabled: false,
}

const baseSchema: SourceSchemaResponse = {
  source_id: 'steam',
  plugin: 'steam',
  plugin_display_name: 'Steam',
  sync_intervals: [
    { key: 'off', label: 'Off' },
    { key: '6h', label: 'Every 6 hours' },
  ],
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
  sync_interval: '6h',
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

  it('emits sync with the source id when the Sync button is clicked', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    await flushPromises()

    await wrapper.find('[data-testid="sync-btn-steam"]').trigger('click')
    expect(wrapper.emitted('sync')).toEqual([['steam']])
  })

  function primeStore(
    store: ReturnType<typeof useDataStore>,
    cfg: SourceConfigResponse,
    oauth?: OAuthStatus,
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
    const loadOAuthStatus = vi
      .spyOn(store, 'loadOAuthStatus')
      .mockImplementation(async (id: string) => {
        if (oauth) store.oauthStatus[id] = oauth
      })
    return { loadSchema, loadConfig, loadOAuthStatus }
  }

  it('clicking the trigger loads schema and config and expands the panel', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  it('shows the config form and enabled toggle once migrated', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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
      props: { source: baseSource, syncing: false },
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

  it('migrates once when Migrate to DB is activated twice in flight', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    const store = useDataStore()
    primeStore(store, yamlConfig)
    const migrate = vi
      .spyOn(store, 'migrateSource')
      .mockImplementation(() => new Promise<never>(() => {}))

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    const button = wrapper.find('[data-testid="migrate-btn-steam"]')
    await button.trigger('click')
    await button.trigger('click')

    expect(migrate).toHaveBeenCalledTimes(1)
  })

  it('disables the Sync button and shows a Disabled badge when source.enabled is false', () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: disabledSource, syncing: false },
    })

    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('aria-label')).toContain('source is disabled')
    expect(wrapper.text()).toContain('Disabled')
  })

  it('clicking the Disable button on an enabled source calls store.setSourceEnabled(false)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  it('saving the form forwards the values to store.updateSourceConfig', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    const update = vi
      .spyOn(store, 'updateSourceConfig')
      .mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()

    await wrapper.find('input[name="vanity_url"]').setValue('updated')
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    expect(update).toHaveBeenCalledTimes(1)
    expect(update.mock.calls[0][0]).toBe('steam')
    expect(update.mock.calls[0][1]).toMatchObject({ vanity_url: 'updated' })
  })

  describe('a detail load that fails', () => {
    async function expandFailing() {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      const schema = vi
        .spyOn(store, 'loadSourceSchema')
        .mockRejectedValue(new Error('backend is down'))
      vi.spyOn(store, 'loadSourceConfig').mockRejectedValue(new Error('backend is down'))

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store, schema }
    }

    it('renders the reason and a Retry instead of an empty panel', async () => {
      const { wrapper } = await expandFailing()

      expect(wrapper.get('[data-testid="details-error-steam"]').text()).toContain(
        'backend is down',
      )
      expect(wrapper.find('[data-testid="details-retry-steam"]').exists()).toBe(true)
      wrapper.unmount()
    })

    it('renders the settings and takes the keyboard there when Retry succeeds', async () => {
      const { wrapper, store } = await expandFailing()
      primeStore(store, migratedConfig)

      await wrapper.find('[data-testid="details-retry-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="form-save"]').exists()).toBe(true)
      expect(document.activeElement).toBe(
        wrapper.get('[data-testid="details-body-steam"]').element,
      )
      wrapper.unmount()
    })

    it('says a second Retry failed rather than changing nothing on screen', async () => {
      const { wrapper } = await expandFailing()

      await wrapper.find('[data-testid="details-retry-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="details-message-steam"]').text()).toContain(
        'Still could not load',
      )
      wrapper.unmount()
    })

    it('says so when Migrate to DB is refused, rather than only restoring the label', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, yamlConfig)
      vi.spyOn(store, 'migrateSource').mockRejectedValue(new Error('already migrated'))

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      await wrapper.find('[data-testid="migrate-btn-steam"]').trigger('click')
      await flushPromises()

      const alert = wrapper.get('[data-testid="migrate-error-steam"]')
      expect(alert.text()).toContain('already migrated')
      expect(document.activeElement).toBe(alert.element)
      wrapper.unmount()
    })
  })

  describe('removing the source', () => {
    async function expandWith(
      deleteSource: () => Promise<void>,
      { focusRemove = false } = {},
    ) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      const remove = vi.spyOn(store, 'deleteSource').mockImplementation(deleteSource)

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const button = wrapper.get('[data-testid="remove-btn-steam"]')
      // jsdom does not focus on click the way a browser does.
      if (focusRemove) (button.element as HTMLElement).focus()
      await button.trigger('click')
      return { wrapper, remove }
    }

    it('asks in the panel rather than in a browser dialog, and removes once answered', async () => {
      const { wrapper, remove } = await expandWith(async () => {})

      expect(remove).not.toHaveBeenCalled()
      expect(wrapper.get('[data-testid="confirm-panel"]').text()).toContain('Steam')

      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      expect(remove).toHaveBeenCalledWith('steam')
      wrapper.unmount()
    })

    it('keeps the source and the keyboard on Remove when the question is declined', async () => {
      const { wrapper, remove } = await expandWith(async () => {}, { focusRemove: true })

      await wrapper.find('[data-testid="confirm-panel-cancel"]').trigger('click')
      await flushPromises()

      expect(remove).not.toHaveBeenCalled()
      expect(document.activeElement).toBe(
        wrapper.get('[data-testid="remove-btn-steam"]').element,
      )
      wrapper.unmount()
    })

    it('will not remove the source from a Remove a running sync has disabled', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      const remove = vi.spyOn(store, 'deleteSource').mockImplementation(async () => {})

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const button = wrapper.get('[data-testid="remove-btn-steam"]')
      ;(button.element as HTMLElement).focus()
      await wrapper.setProps({ syncing: true })

      expect(document.activeElement).toBe(button.element)
      expect(button.attributes('aria-disabled')).toBe('true')

      await button.trigger('click')

      expect(wrapper.find('[data-testid="confirm-panel"]').exists()).toBe(false)
      expect(remove).not.toHaveBeenCalled()
      wrapper.unmount()
    })

    it('says so when the removal is refused, instead of only ending the spinner', async () => {
      const { wrapper } = await expandWith(async () => {
        throw new Error('source is syncing')
      })

      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      const alert = wrapper.get('[data-testid="remove-error-steam"]')
      expect(alert.text()).toContain('source is syncing')
      expect(document.activeElement).toBe(alert.element)
      wrapper.unmount()
    })
  })

  describe('storing and clearing a secret', () => {
    async function expand(attach = false) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
        ...(attach ? { attachTo: document.body } : {}),
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('confirms a stored key only after the request resolves', async () => {
      const { wrapper, store } = await expand()
      vi.spyOn(store, 'setSourceSecret').mockResolvedValue(undefined)

      await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
      await wrapper.find('input[name="api_key"]').setValue('rotated')
      await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="secret-saved-api_key"]').text()).toBe(
        'api_key saved',
      )
      expect(wrapper.find('input[name="api_key"]').exists()).toBe(false)
    })

    it('reports a refused store instead of closing the row as if it worked', async () => {
      const { wrapper, store } = await expand()
      vi.spyOn(store, 'setSourceSecret').mockRejectedValue(new Error('key rejected'))

      await wrapper.find('[data-testid="secret-replace-api_key"]').trigger('click')
      await wrapper.find('input[name="api_key"]').setValue('rotated')
      await wrapper.find('[data-testid="secret-save-api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="secret-error-api_key"]').text()).toContain(
        'key rejected',
      )
      expect(wrapper.find('input[name="api_key"]').exists()).toBe(true)
    })

    it('destroys the credential only once the confirmation is answered', async () => {
      const { wrapper, store } = await expand()
      const clear = vi.spyOn(store, 'clearSourceSecret').mockResolvedValue(undefined)

      await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')
      expect(clear).not.toHaveBeenCalled()

      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      expect(clear).toHaveBeenCalledWith('steam', 'api_key')
    })

    it('reports a refused clear rather than leaving the row unchanged and silent', async () => {
      const { wrapper, store } = await expand()
      vi.spyOn(store, 'clearSourceSecret').mockRejectedValue(new Error('still in use'))

      await wrapper.find('[data-testid="secret-clear-api_key"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="secret-error-api_key"]').text()).toContain(
        'still in use',
      )
    })
  })

  it('renders the Error status pill when updateSourceConfig rejects', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  describe('schedule and run history', () => {
    function failedRun(message: string) {
      return {
        source_id: 'steam',
        started_at: TWO_HOURS_AGO,
        finished_at: TWO_HOURS_AGO,
        status: 'failed',
        items_added: 0,
        items_updated: 0,
        items_unchanged: 0,
        total_items: 0,
        errors: [message],
        omitted_errors: 0,
      }
    }

    it('states the last run, its outcome and the next one without expanding', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })

      const line = wrapper.get('[data-testid="sync-schedule-steam"]').text()
      expect(line).toContain('2 hours ago')
      // In words, never a colour alone (WCAG 1.4.1).
      expect(line).toContain('succeeded')
      expect(line).toContain('in 6 hours')
      expect(
        wrapper.find('button.accordion-trigger').attributes('aria-expanded'),
      ).toBe('false')
    })

    it('says a source has never synced rather than rendering the line blank', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: neverSyncedSource, syncing: false },
      })

      const line = wrapper.get('[data-testid="sync-schedule-steam"]')
      expect(line.text()).toContain('Never synced')
      expect(line.text()).toContain('Next run due now')
    })

    it('reports no next run for a source switched off', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: unscheduledSource, syncing: false },
      })

      expect(wrapper.get('[data-testid="sync-schedule-steam"]').text()).not.toContain(
        'Next run',
      )
    })

    it('offers the schema cadence options and forwards a change', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      const setSchedule = vi
        .spyOn(store, 'setSourceSchedule')
        .mockResolvedValue(undefined)

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      // Hardcoding these in TypeScript is how the two interfaces drift.
      const select = wrapper.get('[data-testid="cadence-select-steam"]')
      expect(select.findAll('option').map((option) => option.text())).toEqual([
        'Off',
        'Every 6 hours',
      ])
      expect((select.element as HTMLSelectElement).value).toBe('6h')

      await select.setValue('off')
      await flushPromises()
      expect(setSchedule).toHaveBeenCalledWith('steam', 'off')
      // Cleared on a timer rather than at once: clearing announces nothing.
      expect(wrapper.get('[data-testid="cadence-status-steam"]').text()).toContain(
        'Cadence saved',
      )
    })

    it('persists the last cadence of a burst typed while a save is in flight', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      let releaseFirst: () => void = () => {}
      const setSchedule = vi
        .spyOn(store, 'setSourceSchedule')
        .mockImplementationOnce(
          () => new Promise<void>((resolve) => (releaseFirst = resolve)),
        )
        .mockResolvedValue(undefined)

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const select = wrapper.get('[data-testid="cadence-select-steam"]')
      await select.setValue('off')
      await select.setValue('6h')
      releaseFirst()
      await flushPromises()

      expect(setSchedule.mock.calls.map((call) => call[1])).toEqual(['off', '6h'])
    })

    it('keeps the chosen cadence on screen while the save is in flight', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      vi.spyOn(store, 'setSourceSchedule').mockImplementation(
        () => new Promise<void>(() => {}),
      )

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const select = wrapper.get('[data-testid="cadence-select-steam"]')
      await select.setValue('off')
      await flushPromises()

      expect((select.element as HTMLSelectElement).value).toBe('off')
      expect(wrapper.get('[data-testid="cadence-status-steam"]').text()).toContain(
        'Saving',
      )
    })

    it('reports a refused cadence change instead of swallowing it', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      vi.spyOn(store, 'setSourceSchedule').mockRejectedValue(
        new Error('Source is not migrated to the database'),
      )

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const select = wrapper.get('[data-testid="cadence-select-steam"]')
      await select.setValue('off')
      await flushPromises()

      const status = wrapper.get('[data-testid="cadence-status-steam"]')
      expect(status.text()).toContain('not migrated to the database')
      expect(status.attributes('role')).toBe('alert')
      // Back to the server's cadence, with the alert beside it saying why.
      expect((select.element as HTMLSelectElement).value).toBe('6h')
      expect(select.attributes('aria-describedby')).toBe(status.attributes('id'))
    })

    it('clears a refusal the operator read before collapsing the row', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      vi.spyOn(store, 'setSourceSchedule').mockRejectedValue(new Error('refused'))

      const trigger = wrapper.find('button.accordion-trigger')
      await trigger.trigger('click')
      await flushPromises()
      await wrapper.get('[data-testid="cadence-select-steam"]').setValue('off')
      await flushPromises()
      await trigger.trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="cadence-status-steam"]').text()).toBe('')
    })

    it('keeps a refusal that landed while the row was collapsed', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, migratedConfig)
      vi.spyOn(store, 'setSourceSchedule').mockRejectedValue(new Error('refused'))

      const trigger = wrapper.find('button.accordion-trigger')
      await trigger.trigger('click')
      await flushPromises()
      // Unawaited on purpose: the refusal must settle after the collapse.
      void wrapper.get('[data-testid="cadence-select-steam"]').setValue('off')
      await trigger.trigger('click')
      await flushPromises()
      await trigger.trigger('click')
      await flushPromises()

      const status = wrapper.get('[data-testid="cadence-status-steam"]')
      expect(status.text()).toContain('refused')
      expect(status.attributes('role')).toBe('alert')
    })

    it('fetches the run history when the disclosure opens, not on page load', async () => {
      // Spied before the mount, or the page-load half below asserts nothing.
      const store = useDataStore()
      const loadRuns = vi
        .spyOn(store, 'loadSourceRuns')
        .mockImplementation(async (id: string) => {
          store.sourceRuns[id] = [failedRun('Steam API returned 401 Unauthorized')]
          return store.sourceRuns[id]
        })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      await flushPromises()

      expect(loadRuns).not.toHaveBeenCalled()

      const toggle = wrapper.get('[data-testid="run-history-toggle-steam"]')
      expect(toggle.attributes('aria-expanded')).toBe('false')
      await toggle.trigger('click')
      await flushPromises()

      expect(loadRuns).toHaveBeenCalledWith('steam')
      expect(toggle.attributes('aria-expanded')).toBe('true')
      // As reported: a failed sync showed a count and no way to see the cause.
      expect(wrapper.get('[data-testid="run-history-steam"]').text()).toContain(
        'Steam API returned 401 Unauthorized',
      )
    })

    it('reports a run-history read that failed rather than saying nothing', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      vi.spyOn(store, 'loadSourceRuns').mockRejectedValue(
        new Error('Service Unavailable'),
      )

      await wrapper.find('[data-testid="run-history-toggle-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="run-history-status-steam"]').text()).toContain(
        'Service Unavailable',
      )
    })

    it('announces that the runs arrived rather than going quiet on success', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      vi.spyOn(store, 'loadSourceRuns').mockImplementation(async (id: string) => {
        store.sourceRuns[id] = [failedRun('Steam API returned 401 Unauthorized')]
        return store.sourceRuns[id]
      })

      await wrapper.find('[data-testid="run-history-toggle-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="run-history-status-steam"]').text()).not.toBe(
        '',
      )
    })

    it('says something when a source has no runs instead of opening onto nothing', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: neverSyncedSource, syncing: false },
      })
      const store = useDataStore()
      vi.spyOn(store, 'loadSourceRuns').mockResolvedValue([])

      await wrapper.find('[data-testid="run-history-toggle-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="run-history-status-steam"]').text()).not.toBe(
        '',
      )
    })

    it('shows the item errors of a run that completed, not only of a failed one', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const store = useDataStore()
      const partial = { ...failedRun('book 12 has no title'), status: 'completed' }
      vi.spyOn(store, 'loadSourceRuns').mockImplementation(async (id: string) => {
        store.sourceRuns[id] = [partial]
        return store.sourceRuns[id]
      })

      await wrapper.find('[data-testid="run-history-toggle-steam"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="run-history-steam"]').text()).toContain(
        'book 12 has no title',
      )
    })
  })

  // Each OAuth source below is named for its purpose, not for its plugin: the
  // connect flow is chosen by plugin and addressed by source id.
  describe('trakt device-code connect/disconnect', () => {
    const traktSource = {
      ...baseSource,
      id: 'trakt_work',
      display_name: 'Trakt (work)',
      plugin_display_name: 'Trakt',
    }
    const traktConfig: SourceConfigResponse = {
      ...migratedConfig,
      source_id: 'trakt_work',
      plugin: 'trakt',
      plugin_display_name: 'Trakt',
    }

    async function expandTrakt(connected: boolean) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, traktConfig, { enabled: true, connected, authUrl: null })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('renders the device-code connect flow when trakt is not connected', async () => {
      const { wrapper } = await expandTrakt(false)

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        true,
      )
      expect(
        wrapper.find('[data-testid="disconnect-btn-trakt_work"]').exists(),
      ).toBe(false)
    })

    it('clicking Disconnect names the source being disconnected', async () => {
      const { wrapper, store } = await expandTrakt(true)
      const disconnect = vi
        .spyOn(store, 'disconnectTrakt')
        .mockResolvedValue(undefined)

      await wrapper
        .find('[data-testid="disconnect-btn-trakt_work"]')
        .trigger('click')

      expect(disconnect).toHaveBeenCalledWith('trakt_work')
    })

    it('disconnects once when Disconnect is activated twice in flight', async () => {
      const { wrapper, store } = await expandTrakt(true)
      // The second DELETE 404s, and the panel reported the connection status
      // unreadable after a disconnect that had in fact worked.
      const disconnect = vi
        .spyOn(store, 'disconnectTrakt')
        .mockImplementation(() => new Promise<never>(() => {}))

      const button = wrapper.find('[data-testid="disconnect-btn-trakt_work"]')
      await button.trigger('click')
      await button.trigger('click')

      expect(disconnect).toHaveBeenCalledTimes(1)
      expect(wrapper.find('[data-testid="oauth-status-error"]').exists()).toBe(false)
    })

    it('carries a trakt disconnect failure in the panel live region', async () => {
      const { wrapper, store } = await expandTrakt(true)

      // Trakt used to be excluded from the panel's only live region, so a
      // refused disconnect left the button, the "connected" label and no word
      // anywhere that it had failed (WCAG 3.3.1).
      const region = wrapper.get('[data-testid="oauth-message"]')
      expect(region.attributes('aria-live')).toBe('polite')

      store.oauthMessages['trakt_work'] = 'Error: No active Trakt connection found'
      await flushPromises()

      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'No active Trakt connection found',
      )
    })

    it('moves focus to the OAuth panel after a disconnect removes the button', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, traktConfig, {
        enabled: true,
        connected: true,
        authUrl: null,
      })
      vi.spyOn(store, 'disconnectTrakt').mockImplementation(async (id) => {
        store.oauthStatus[id] = { enabled: true, connected: false, authUrl: null }
      })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const disconnect = wrapper.get('[data-testid="disconnect-btn-trakt_work"]')
      ;(disconnect.element as HTMLElement).focus()
      await disconnect.trigger('click')
      await flushPromises()

      // The button unmounts itself as it succeeds; without a deliberate move
      // focus falls to <body> and keyboard users restart from the top.
      expect(document.activeElement).toBe(
        wrapper.get('.source-connect').element,
      )
      wrapper.unmount()
    })

    it('says so when the connection status cannot be read', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      const { loadOAuthStatus } = primeStore(store, traktConfig)
      loadOAuthStatus.mockRejectedValueOnce(new Error('status read failed'))

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="oauth-status-error"]').text()).toContain(
        'Could not read',
      )
      // The fallback status reads as "not connected", which would offer a
      // Connect button hinting at credentials that may be perfectly fine.
      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(false)
      wrapper.unmount()
    })
  })

  describe('gog/epic connect/disconnect', () => {
    const gogSource = {
      ...baseSource,
      id: 'gog_work',
      display_name: 'GOG (work)',
      plugin_display_name: 'GOG',
    }
    const gogConfig: SourceConfigResponse = {
      ...migratedConfig,
      source_id: 'gog_work',
      plugin: 'gog',
      plugin_display_name: 'GOG',
    }

    async function expandGog(
      connected: boolean,
      authUrl: string | null = 'https://auth.gog.com/auth',
    ) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: gogSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, gogConfig, {
        enabled: true,
        connected,
        authUrl,
      })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('submitting the code names the source being connected', async () => {
      const { wrapper, store } = await expandGog(false)
      const submit = vi.spyOn(store, 'submitGogCode').mockResolvedValue(undefined)

      const flow = wrapper.findComponent(OAuthConnectFlow)
      expect(flow.props('authUrl')).toBe('https://auth.gog.com/auth')
      flow.vm.$emit('submit', 'auth-code')
      await flushPromises()

      expect(submit).toHaveBeenCalledWith('gog_work', 'auth-code')
    })

    it('reports the status as unknown when a disconnect cannot be re-read', async () => {
      const { wrapper, store } = await expandGog(true)
      // The DELETE succeeded and said so; only the re-read after it failed.
      vi.spyOn(store, 'disconnectGog').mockImplementation(async (id: string) => {
        store.oauthMessages[id] = 'Disconnected. You can reconnect below.'
        throw new Error('status read failed')
      })

      await wrapper.get('[data-testid="disconnect-btn-gog_work"]').trigger('click')
      await flushPromises()

      // The cached flag still says connected, so rendering it put "GOG account
      // connected." and a Disconnect button beside a region announcing the
      // disconnect, with nothing saying which one is current.
      expect(store.oauthStatusFor('gog_work').connected).toBe(true)
      expect(wrapper.find('[data-testid="oauth-connected"]').exists()).toBe(false)
      expect(
        wrapper.find('[data-testid="disconnect-btn-gog_work"]').exists(),
      ).toBe(false)
      expect(wrapper.get('[data-testid="oauth-status-error"]').text()).toContain(
        'Could not read',
      )
      expect(wrapper.find('[data-testid="oauth-status-retry"]').exists()).toBe(true)
    })

    // The Epic branch is `v-else-if="isEpic"`, so a third OAuth plugin cannot
    // silently inherit Epic's flow, origin and label.
    const epicSource = {
      ...baseSource,
      id: 'epic_work',
      display_name: 'Epic (work)',
      plugin_display_name: 'Epic Games',
    }
    const epicConfig: SourceConfigResponse = {
      ...migratedConfig,
      source_id: 'epic_work',
      plugin: 'epic_games',
      plugin_display_name: 'Epic Games',
    }

    async function expandEpic(connected: boolean) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: epicSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(store, epicConfig, {
        enabled: true,
        connected,
        authUrl: 'https://www.epicgames.com/id/api/redirect',
      })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('renders the Epic flow, not the GOG one, for an epic_games source', async () => {
      const { wrapper, store } = await expandEpic(false)
      const submit = vi.spyOn(store, 'submitEpicCode').mockResolvedValue(undefined)
      const gogSubmit = vi.spyOn(store, 'submitGogCode')

      const flow = wrapper.findComponent(OAuthConnectFlow)
      expect(flow.props('serviceName')).toBe('Epic Games')
      expect(flow.props('expectedOrigin')).toBe('https://www.epicgames.com')
      flow.vm.$emit('submit', 'auth-code')
      await flushPromises()

      expect(submit).toHaveBeenCalledWith('epic_work', 'auth-code')
      expect(gogSubmit).not.toHaveBeenCalled()
    })
  })

  describe('progress + error rendering driven by the job prop', () => {
    function makeJob(overrides: Record<string, unknown> = {}) {
      return {
        source: 'Steam',
        status: 'running' as const,
        started_at: null,
        completed_at: null,
        items_processed: 4,
        total_items: 10,
        current_item: 'Half-Life 2',
        current_source: 'Steam',
        error_message: null,
        progress_percent: 40,
        items_added: 0,
        items_updated: 0,
        items_unchanged: 0,
        errors: [] as SyncErrorResponse[],
        sources: [] as SyncSourceProgressResponse[],
        ...overrides,
      }
    }

    it('announces progress outside the trigger, whose name polls leave alone', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false },
      })
      const trigger = wrapper.get('button.accordion-trigger')
      const idleName = trigger.text()
      expect(wrapper.get('.source-progress-counts').text()).toBe('')

      await wrapper.setProps({ syncing: true, job: makeJob() })
      await wrapper.setProps({ job: makeJob({ items_processed: 9, progress_percent: 90 }) })

      expect(trigger.find('[role="progressbar"]').exists()).toBe(false)
      expect(trigger.text()).toBe(idleName)
      expect(wrapper.get('.source-progress-counts').text()).toContain('9/10')
    })

    it('keeps the progress region out of the panel a collapsed row hides', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job: makeJob() },
      })

      expect(
        wrapper.get('button.accordion-trigger').attributes('aria-expanded'),
      ).toBe('false')
      expect(wrapper.get('.source-progress-counts').element.closest('[hidden]')).toBeNull()
    })

    it('renders progress bar from a single-source running job', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job: makeJob() },
      })

      const bar = wrapper.find('[role="progressbar"]')
      expect(bar.exists()).toBe(true)
      expect(bar.attributes('aria-valuenow')).toBe('40')
      expect(wrapper.text()).toContain('4/10')
      expect(wrapper.text()).toContain('40%')
      expect(wrapper.text()).toContain('Half-Life 2')
    })

    it('looks up this source in job.sources[] when job is umbrella', () => {
      const job = makeJob({
        source: 'All Sources',
        items_processed: 100,
        total_items: 200,
        progress_percent: 50,
        current_item: 'Other thing',
        sources: [
          {
            source: 'Steam',
            items_processed: 7,
            total_items: 8,
            current_item: 'Portal 2',
            progress_percent: 87,
            items_added: 7,
            items_updated: 0,
            items_unchanged: 0,
            omitted_errors: 0,
          },
        ],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job },
      })

      const bar = wrapper.find('[role="progressbar"]')
      expect(bar.attributes('aria-valuenow')).toBe('87')
      expect(wrapper.text()).toContain('7/8')
      expect(wrapper.text()).toContain('Portal 2')
      // The umbrella job's top-level current_item ("Other thing") is for
      // a different source — it must NOT leak into this accordion.
      expect(wrapper.text()).not.toContain('Other thing')
    })

    it('bounds the error list and states the server total exactly once', () => {
      // This renders outside the collapsible panel, so a list as long as the
      // run's failures pushes every other source's Sync button off the page.
      const reported = 200
      const omitted = 4800
      // Sonarr's failures come first, so capping before filtering renders
      // another source's errors here, or none at all.
      const failures = (source: string) =>
        Array.from({ length: reported }, (_, i) => ({
          source,
          message: `${source} ${i} failed`,
        }))
      const slot = (source: string) => ({
        source,
        items_processed: 0,
        total_items: reported + omitted,
        current_item: null,
        progress_percent: 0,
        items_added: 0,
        items_updated: 0,
        items_unchanged: 0,
        omitted_errors: omitted,
      })
      const job = makeJob({
        source: 'All Sources',
        status: 'completed',
        errors: [...failures('Sonarr'), ...failures('Steam')],
        sources: [slot('Sonarr'), slot('Steam')],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const items = wrapper.get('[data-testid="source-sync-errors"]').findAll('li')
      expect(items.length).toBeGreaterThan(0)
      expect(items.length).toBeLessThan(reported)
      expect(items.every((li) => li.text().startsWith('Steam '))).toBe(true)
      const tails = wrapper.findAll('[data-testid="source-sync-errors-more"]')
      expect(tails).toHaveLength(1)
      expect(tails[0].text()).toContain(String(reported + omitted))
    })

    it('shows only the errors belonging to this source', () => {
      const job = makeJob({
        source: 'All Sources',
        status: 'completed',
        errors: [
          { source: 'Sonarr', message: 'TLS verification failed' },
          { source: 'Steam', message: 'Rate limit exceeded' },
        ],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const errors = wrapper.get('[data-testid="source-sync-errors"]')
      expect(errors.findAll('li').map((li) => li.text())).toEqual([
        'Rate limit exceeded',
      ])
      expect(wrapper.text()).not.toContain('TLS verification failed')
    })
  })
})
