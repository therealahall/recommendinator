import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SyncSourceAccordion from './SyncSourceAccordion.vue'
import { ApiError } from '@/composables/useApi'
import { useDataStore } from '@/stores/data'
import type {
  SourceConfigResponse,
  SourceSchemaResponse,
  SyncSourceResponse,
} from '@/types/api'

const baseSource: SyncSourceResponse = {
  id: 'steam',
  display_name: 'Steam',
  plugin_display_name: 'Steam',
  enabled: true,
  is_file_import: false,
}

const disabledSource: SyncSourceResponse = {
  ...baseSource,
  enabled: false,
}

// A leftover entry naming a file-import plugin: it still carries enabled=true
// (nothing rewrites the stored flag), which is exactly why the accordion has
// to key its "not syncable" rendering off is_file_import rather than enabled.
const fileImportSource: SyncSourceResponse = {
  id: 'legacy_books',
  display_name: 'Legacy Books',
  plugin_display_name: 'Goodreads (CSV Export)',
  enabled: true,
  is_file_import: true,
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

// v-show toggles inline `display`. Read that rather than `isVisible()`:
// jsdom's computed style goes stale after a visible → hidden transition, which
// is exactly the transition the retry test needs to observe.
function isShown(element: Element): boolean {
  return (element as HTMLElement).style.display !== 'none'
}

describe('SyncSourceAccordion', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('shows only the source name and Sync button when collapsed', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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
      props: { source: baseSource, syncing: false },
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

  it('shows the Migrate to DB button when the source is not yet migrated', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  it('renders the Sync button label as Syncing… while syncing', () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: true },
    })

    const sync = wrapper.find('[data-testid="sync-btn-steam"]')
    expect(sync.text()).toBe('Syncing…')
    expect(sync.attributes('disabled')).toBeDefined()
    expect(sync.attributes('aria-label')).toContain('Syncing Steam')
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

  describe('a leftover file-import entry', () => {
    it('renders no Sync button and a Not syncable badge', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: fileImportSource, syncing: false },
      })

      // Keyed off is_file_import, not enabled: the row is still enabled=true.
      expect(wrapper.find('[data-testid="sync-btn-legacy_books"]').exists()).toBe(
        false,
      )
      expect(wrapper.text()).toContain('Not syncable')
      expect(wrapper.text()).not.toContain('Disabled')
    })

    it('explains itself and offers Remove without loading a schema', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: fileImportSource, syncing: false },
      })
      const store = useDataStore()
      // Both per-source endpoints 404 for a file-import plugin, so expanding
      // must not call them — an error banner would be all the user got.
      const loadSchema = vi.spyOn(store, 'loadSourceSchema')
      const loadConfig = vi.spyOn(store, 'loadSourceConfig')

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      expect(loadSchema).not.toHaveBeenCalled()
      expect(loadConfig).not.toHaveBeenCalled()
      expect(wrapper.text()).toContain('one-shot file import')
      expect(wrapper.text()).toContain('config.yaml')
      expect(
        wrapper.find('[data-testid="remove-btn-legacy_books"]').exists(),
      ).toBe(true)
    })

    async function expandAndRemove() {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: fileImportSource, syncing: false },
      })
      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      await wrapper
        .find('[data-testid="remove-btn-legacy_books"]')
        .trigger('click')
      await flushPromises()
      return wrapper
    }

    it('removes the row and hands the parent the name to announce', async () => {
      const store = useDataStore()
      const remove = vi
        .spyOn(store, 'deleteSource')
        .mockResolvedValue(undefined)

      const wrapper = await expandAndRemove()

      expect(remove).toHaveBeenCalledWith('legacy_books')
      // The parent has to do the announcing and re-focusing: this component
      // unmounts with the row it just deleted.
      expect(wrapper.emitted('removed')).toEqual([['Legacy Books']])
      expect(isShown(wrapper.find('[role="alert"]').element)).toBe(false)
    })

    it('reports a failed removal instead of silently doing nothing', async () => {
      // Regression: the banner interpolated `err.message`, which ApiError
      // builds from the status line — so the one failure this flow routinely
      // hits announced "Couldn't remove this source: 404 Not Found" and the
      // server's actual explanation was discarded (WCAG 3.3.1).
      const store = useDataStore()
      // Exactly what DELETE /api/sync/sources/<id> answers for a YAML-only
      // entry: 404 not_migrated, with the explanation in `detail`.
      vi.spyOn(store, 'deleteSource').mockRejectedValue(
        new ApiError(404, 'Not Found', {
          detail: 'Source has not been migrated to the database.',
        }),
      )

      const wrapper = await expandAndRemove()

      const alert = wrapper.find('[role="alert"]')
      // Mounted before the text arrives (v-show, not v-if) so screen readers
      // that ignore a region inserted with content still announce it.
      expect(alert.exists()).toBe(true)
      expect(isShown(alert.element)).toBe(true)
      expect(alert.text()).toBe(
        "Couldn't remove this source: Source has not been migrated to the " +
          'database.',
      )
      expect(alert.text()).not.toContain('404')
      expect(wrapper.emitted('removed')).toBeUndefined()
    })

    it('falls back to generic copy when the rejection carries no detail', async () => {
      const store = useDataStore()
      // A dropped connection is not an ApiError at all, and its message
      // ("Failed to fetch") is no more use to the user than a status line.
      vi.spyOn(store, 'deleteSource').mockRejectedValue(
        new Error('Failed to fetch'),
      )

      const wrapper = await expandAndRemove()

      const alert = wrapper.find('[role="alert"]')
      expect(alert.text()).toBe("Couldn't remove this source.")
      expect(wrapper.emitted('removed')).toBeUndefined()
    })

    it('clears the failure alert when the user retries', async () => {
      const store = useDataStore()
      const remove = vi
        .spyOn(store, 'deleteSource')
        .mockRejectedValueOnce(
          new ApiError(404, 'Not Found', {
            detail: 'Source has not been migrated to the database.',
          }),
        )
        .mockResolvedValueOnce(undefined)

      const wrapper = await expandAndRemove()
      expect(isShown(wrapper.find('[role="alert"]').element)).toBe(true)

      await wrapper
        .find('[data-testid="remove-btn-legacy_books"]')
        .trigger('click')
      await flushPromises()

      expect(remove).toHaveBeenCalledTimes(2)
      // A stale error beside a now-successful action would contradict itself,
      // and a permanently-mounted region must be emptied as well as hidden or
      // a screen reader can still reach the old text.
      expect(wrapper.find('[role="alert"]').text()).toBe('')
      expect(isShown(wrapper.find('[role="alert"]').element)).toBe(false)
      expect(wrapper.emitted('removed')).toEqual([['Legacy Books']])
    })
  })

  it('does not emit sync when disabled source button is clicked', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: disabledSource, syncing: false },
    })

    await wrapper.find('[data-testid="sync-btn-steam"]').trigger('click')
    expect(wrapper.emitted('sync')).toBeUndefined()
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

  it('clicking the Enable button on a disabled source calls store.setSourceEnabled(true)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  it('clicking Remove with confirm=false does NOT call store.deleteSource', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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
      props: { source: baseSource, syncing: false },
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

  it('renders the server detail in the Error status pill when updateSourceConfig rejects', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    vi.spyOn(store, 'updateSourceConfig').mockRejectedValue(
      new ApiError(400, 'Bad Request', {
        detail: "Invalid field 'vanity_url' for plugin 'steam'.",
      }),
    )

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.exists()).toBe(true)
    expect(status.text()).toContain("Invalid field 'vanity_url'")
    // Not the status line ApiError builds its message from.
    expect(status.text()).not.toContain('400')
    expect(status.attributes('role')).toBe('alert')
  })

  it('falls back to generic save copy when the rejection carries no detail', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    const store = useDataStore()
    primeStore(store, migratedConfig)
    vi.spyOn(store, 'updateSourceConfig').mockRejectedValue(
      new Error('Failed to fetch'),
    )

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="form-save"]').trigger('click')
    await flushPromises()

    const status = wrapper.find('[data-testid="form-save-status"]')
    expect(status.text()).toContain("Couldn't save these settings.")
  })

  it('disables the toggle button while setSourceEnabled is in flight (re-entrant guard)', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
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

  describe('trakt device-code connect/disconnect', () => {
    const traktSource: SyncSourceResponse = {
      ...baseSource,
      id: 'trakt',
      display_name: 'Trakt',
      plugin_display_name: 'Trakt',
    }
    const traktConfig: SourceConfigResponse = {
      ...migratedConfig,
      source_id: 'trakt',
      plugin: 'trakt',
      plugin_display_name: 'Trakt',
    }

    async function expandTrakt(connected: boolean) {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
      })
      const store = useDataStore()
      store.$patch({ traktStatus: { enabled: true, connected } })
      primeStore(store, traktConfig)

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store }
    }

    it('renders the device-code connect flow when trakt is not connected', async () => {
      const { wrapper } = await expandTrakt(false)

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        true,
      )
      expect(wrapper.find('[data-testid="disconnect-btn-trakt"]').exists()).toBe(
        false,
      )
    })

    it('renders a connected state with a disconnect button when connected', async () => {
      const { wrapper } = await expandTrakt(true)

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        false,
      )
      const connected = wrapper.find('[data-testid="trakt-connected"]')
      expect(connected.exists()).toBe(true)
      // role="status" lets a screen reader self-describe the connected state
      // when a user lands on an already-connected source.
      expect(connected.attributes('role')).toBe('status')
      const disconnect = wrapper.find('[data-testid="disconnect-btn-trakt"]')
      expect(disconnect.exists()).toBe(true)
      expect(disconnect.attributes('aria-label')).toBe('Disconnect Trakt')
    })

    it('clicking Disconnect calls store.disconnectTrakt', async () => {
      const { wrapper, store } = await expandTrakt(true)
      const disconnect = vi
        .spyOn(store, 'disconnectTrakt')
        .mockResolvedValue(undefined)

      await wrapper.find('[data-testid="disconnect-btn-trakt"]').trigger('click')

      expect(disconnect).toHaveBeenCalledTimes(1)
    })

    it('does not render trakt affordances before migration', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
      })
      const store = useDataStore()
      store.$patch({ traktStatus: { enabled: true, connected: false } })
      primeStore(store, { ...traktConfig, migrated: false, migrated_at: null })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        false,
      )
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
        error_count: 0,
        errors: [] as string[],
        sources: [] as never[],
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

      // Regression: the percentage was baked into aria-label, so AT read it
      // twice and the accessible name went stale between polls; and the whole
      // counts region was aria-live="polite", which the 2s poll rewrote into a
      // flood that buried the eventual result (WCAG 4.1.3).
      expect(bar.attributes('aria-label')).toBe('Steam sync progress')
      expect(bar.attributes('aria-valuetext')).toBe('40% complete')
      expect(
        wrapper.find('.source-accordion-progress').attributes('aria-live'),
      ).toBeUndefined()
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

    it('omits the progress bar when progress_percent is null', () => {
      const job = makeJob({ progress_percent: null, total_items: null })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job },
      })

      expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
      // Counts label uses the items-only fallback, NOT a malformed
      // fraction with a null total.
      expect(wrapper.text()).toContain('4 items')
      expect(wrapper.text()).not.toContain('4/null')
      expect(wrapper.text()).not.toContain('4/0')
    })

    it('renders the error badge for a completed job with errors', () => {
      const job = makeJob({
        status: 'completed',
        error_count: 3,
        errors: ['e1', 'e2', 'e3'],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      expect(wrapper.text()).toContain('3 errors')
      const badge = wrapper.find('.source-accordion-error-badge')
      expect(badge.attributes('aria-label')).toBe('3 errors on last sync')
    })

    it('uses singular wording when error_count is 1', () => {
      const job = makeJob({
        status: 'completed',
        error_count: 1,
        errors: ['e1'],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const badge = wrapper.find('.source-accordion-error-badge')
      expect(badge.text()).toBe('1 error')
      expect(badge.attributes('aria-label')).toBe('1 error on last sync')
    })

    it('hides the error badge while a sync is in progress', () => {
      const job = makeJob({
        status: 'running',
        error_count: 5,
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job },
      })

      expect(wrapper.find('.source-accordion-error-badge').exists()).toBe(false)
    })

    it('renders nothing extra when job is null', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job: null },
      })

      // The progress region is in the DOM via v-show but hidden, and the
      // error badge is absent because there's no job.
      expect(wrapper.find('.source-accordion-error-badge').exists()).toBe(false)
      const region = wrapper.find('.source-accordion-progress')
      expect(region.exists()).toBe(true)
      expect((region.element as HTMLElement).style.display).toBe('none')
    })
  })
})
