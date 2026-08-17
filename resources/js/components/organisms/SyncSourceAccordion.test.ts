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
  sync_interval_default: 'daily',
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
  sync_interval_default: 'daily',
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
      props: { source: baseSource, syncing: false },
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

  describe('schedule', () => {
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
      expect(setSchedule).toHaveBeenCalledWith('steam', 'off')
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
      await wrapper.get('[data-testid="cadence-select-steam"]').setValue('off')
      await flushPromises()

      const status = wrapper.get('[data-testid="cadence-status-steam"]')
      expect(status.text()).toContain('not migrated to the database')
      expect(status.attributes('aria-live')).toBe('polite')
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
        wrapper.get('.source-accordion-oauth').element,
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

    it('caps this source after the filter, not job.errors before it', () => {
      // Capping job.errors first leaves a source whose failures all fall past
      // the cap rendering nothing, on the umbrella run that produced them.
      const job = makeJob({
        source: 'All Sources',
        status: 'completed',
        errors: [
          ...Array.from({ length: 6 }, (_, i) => ({
            source: 'Sonarr',
            message: `Sonarr ${i} failed`,
          })),
          ...Array.from({ length: 6 }, (_, i) => ({
            source: 'Steam',
            message: `Steam ${i} failed`,
          })),
        ],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const items = wrapper
        .get('[data-testid="source-sync-errors"]')
        .findAll('li')
        .map((li) => li.text())
      expect(items).toEqual([
        'Steam 0 failed',
        'Steam 1 failed',
        'Steam 2 failed',
        'Steam 3 failed',
        'Steam 4 failed',
        '… and 1 more',
      ])
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
