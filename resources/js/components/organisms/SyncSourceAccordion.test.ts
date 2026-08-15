import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SyncSourceAccordion from './SyncSourceAccordion.vue'
import OAuthConnectFlow from '@/components/molecules/OAuthConnectFlow.vue'
import { useDataStore, type OAuthStatus } from '@/stores/data'
import { componentStyles } from '@/testing/styles'
import type {
  SourceConfigResponse,
  SourceSchemaResponse,
  SyncErrorResponse,
} from '@/types/api'

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

/** What the server answers for a source it will not connect. */
const UNCONNECTABLE: OAuthStatus = {
  enabled: false,
  connected: false,
  authUrl: null,
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

  it('does not announce a connection status for a source that has none', async () => {
    const wrapper = mount(SyncSourceAccordion, {
      props: { source: baseSource, syncing: false },
    })
    const store = useDataStore()
    const { loadOAuthStatus } = primeStore(store, migratedConfig)
    vi.spyOn(store, 'setSourceEnabled').mockResolvedValue(undefined)

    await wrapper.find('button.accordion-trigger').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="form-toggle-enabled"]').trigger('click')
    await flushPromises()

    // Steam has no gate for the re-read to move and no OAuth panel, so the
    // region a message would go to is not on screen to carry it. Still the
    // empty string expanding the panel cleared it to.
    expect(loadOAuthStatus).not.toHaveBeenCalled()
    expect(store.oauthMessages[baseSource.id]).toBe('')
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

  it('suppresses the OAuth panel focus ring for pointer focus alone', () => {
    // Programmatic focus matches :focus-visible when the element that lost
    // focus did, so a blanket `:focus { outline: none }` erased the ring for
    // the keyboard user it exists for (2.4.7). The scoped rule outranks
    // base.css, which cannot rescue it.
    const styles = componentStyles(
      'resources/js/components/organisms/SyncSourceAccordion.vue',
    )

    expect(styles).toMatch(
      /\.source-accordion-oauth:focus:not\(:focus-visible\)\s*\{[^}]*outline:\s*none/,
    )
    expect(styles).not.toMatch(/\.source-accordion-oauth:focus\s*\{/)
  })

  // Each OAuth source below is named for its purpose, not for its plugin: the
  // connect flow is chosen by plugin and addressed by source id.
  describe('trakt device-code connect/disconnect', () => {
    const traktSource = {
      id: 'trakt_work',
      display_name: 'Trakt (work)',
      plugin_display_name: 'Trakt',
      enabled: true,
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

    it('renders a connected state with a disconnect button when connected', async () => {
      const { wrapper } = await expandTrakt(true)

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        false,
      )
      const connected = wrapper.find('[data-testid="oauth-connected"]')
      expect(connected.exists()).toBe(true)
      expect(connected.text()).toBe('Trakt account connected.')
      // Not a live region of its own: it is inserted already populated, which
      // announces nothing, and it repeats what the panel's region carries.
      expect(connected.attributes('role')).toBeUndefined()
      const disconnect = wrapper.find('[data-testid="disconnect-btn-trakt_work"]')
      expect(disconnect.exists()).toBe(true)
      // Named for the source: two expanded Trakt panels would otherwise offer
      // two buttons a screen-reader user cannot tell apart.
      expect(disconnect.attributes('aria-label')).toBe(
        'Disconnect Trakt (work) from Trakt',
      )
    })

    it('names the focus target so landing on it is not a mystery', async () => {
      const { wrapper } = await expandTrakt(true)

      const panel = wrapper.get('.source-accordion-oauth')
      expect(panel.attributes('role')).toBe('group')
      expect(panel.attributes('aria-label')).toBe('Trakt (work) connection')
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

    it('reads the OAuth status for this source when the panel opens', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
      })
      const store = useDataStore()
      const { loadOAuthStatus } = primeStore(store, traktConfig)

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      expect(loadOAuthStatus).toHaveBeenCalledWith('trakt_work', 'trakt')
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

    /** Expand a Trakt panel whose first status read fails, attached so focus
     *  assertions are meaningful. */
    async function expandWithFailedStatus() {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      const { loadOAuthStatus } = primeStore(store, traktConfig)
      loadOAuthStatus.mockRejectedValueOnce(new Error('status read failed'))

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store, loadOAuthStatus }
    }

    it('says so when the connection status cannot be read', async () => {
      const { wrapper } = await expandWithFailedStatus()

      expect(wrapper.get('[data-testid="oauth-status-error"]').text()).toContain(
        'Could not read',
      )
      // Not role="alert": it is inserted already populated, along with the
      // whole accordion body, which JAWS reads as page content and skips.
      expect(wrapper.get('[data-testid="oauth-status-error"]').attributes('role'))
        .toBeUndefined()
      // The fallback status reads as "not connected", which would offer a
      // Connect button hinting at credentials that may be perfectly fine.
      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(false)
      wrapper.unmount()
    })

    it('announces a repeated retry failure, which changes nothing else', async () => {
      const { wrapper, loadOAuthStatus } = await expandWithFailedStatus()
      const region = wrapper.get('[data-testid="oauth-message"]').element
      expect(region.textContent).toBe('')

      loadOAuthStatus.mockRejectedValueOnce(new Error('still down'))
      const retry = wrapper.get('[data-testid="oauth-status-retry"]')
      ;(retry.element as HTMLElement).focus()
      await retry.trigger('click')
      await flushPromises()

      // Same node throughout — a region re-created with its text is skipped.
      expect(wrapper.get('[data-testid="oauth-message"]').element).toBe(region)
      expect(region.textContent).toContain('Still could not read')
      // The Retry button survived a failure, so focus stays on it.
      expect(document.activeElement).toBe(retry.element)
      wrapper.unmount()
    })

    it('announces a recovered status and rehomes the focus Retry took with it', async () => {
      const { wrapper, store, loadOAuthStatus } = await expandWithFailedStatus()
      const region = wrapper.get('[data-testid="oauth-message"]').element

      loadOAuthStatus.mockImplementation(async (id: string) => {
        store.oauthStatus[id] = { enabled: true, connected: false, authUrl: null }
      })
      const retry = wrapper.get('[data-testid="oauth-status-retry"]')
      ;(retry.element as HTMLElement).focus()
      await retry.trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="oauth-status-error"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(true)
      // Success unmounts the Retry button, so it must not vanish silently...
      expect(wrapper.get('[data-testid="oauth-message"]').element).toBe(region)
      expect(region.textContent).toContain('Connection status updated')
      // ...nor drop the keyboard user to <body>.
      expect(document.activeElement).toBe(
        wrapper.get('.source-accordion-oauth').element,
      )
      wrapper.unmount()
    })

    it('tracks the visible Retrying… wording in the accessible name', async () => {
      const { wrapper, loadOAuthStatus } = await expandWithFailedStatus()
      const retry = wrapper.get('[data-testid="oauth-status-retry"]')
      expect(retry.attributes('aria-label')).toBe(
        'Retry the connection status check for Trakt (work)',
      )

      let release: () => void = () => {}
      loadOAuthStatus.mockImplementation(
        () => new Promise<void>((resolve) => { release = resolve }),
      )
      await retry.trigger('click')

      // Speech-input users say the word they can see, so a name still saying
      // "Retry" while the button reads "Retrying…" no longer matches it.
      expect(retry.text()).toBe('Retrying…')
      expect(retry.attributes('aria-label')).toBe(
        'Retrying the connection status check for Trakt (work)',
      )
      release()
      await flushPromises()
      wrapper.unmount()
    })

    it('ignores a second Retry click while the first is in flight', async () => {
      const { wrapper, loadOAuthStatus } = await expandWithFailedStatus()
      let release: () => void = () => {}
      loadOAuthStatus.mockImplementation(
        () => new Promise<void>((resolve) => { release = resolve }),
      )
      loadOAuthStatus.mockClear()

      const retry = wrapper.get('[data-testid="oauth-status-retry"]')
      await retry.trigger('click')
      await retry.trigger('click')

      expect(loadOAuthStatus).toHaveBeenCalledTimes(1)
      expect(retry.attributes('aria-disabled')).toBe('true')
      release()
      await flushPromises()
      wrapper.unmount()
    })

    it('does not render trakt affordances before migration', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: traktSource, syncing: false },
      })
      const store = useDataStore()
      primeStore(
        store,
        { ...traktConfig, migrated: false, migrated_at: null },
        { enabled: true, connected: false, authUrl: null },
      )

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="trakt-connect-btn"]').exists()).toBe(
        false,
      )
    })
  })

  describe('gog/epic connect/disconnect', () => {
    const gogSource = {
      id: 'gog_work',
      display_name: 'GOG (work)',
      plugin_display_name: 'GOG',
      enabled: true,
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

    it('clicking Disconnect names the source being disconnected', async () => {
      const { wrapper, store } = await expandGog(true)
      const disconnect = vi
        .spyOn(store, 'disconnectGog')
        .mockResolvedValue(undefined)

      await wrapper.find('[data-testid="disconnect-btn-gog_work"]').trigger('click')

      expect(disconnect).toHaveBeenCalledWith('gog_work')
      expect(
        wrapper.find('[data-testid="disconnect-btn-gog_work"]').attributes('aria-label'),
      ).toBe('Disconnect GOG (work) from GOG')
    })

    it('gives three GOG sources distinct code field ids', async () => {
      const store = useDataStore()
      // All three run the gog plugin — the configuration this branch exists to
      // allow — and `gog_work`/`gog-work` are both valid source ids that a
      // sanitiser collapsing `_` renders identical.
      primeStore(store, gogConfig, {
        enabled: true,
        connected: false,
        authUrl: 'https://login.gog.com/auth',
      })
      vi.spyOn(window, 'open').mockReturnValue(null)
      const wrappers = ['gog_work', 'gog', 'gog-work'].map((id) =>
        mount(SyncSourceAccordion, {
          props: { source: { ...gogSource, id }, syncing: false },
          attachTo: document.body,
        }),
      )

      for (const wrapper of wrappers) {
        await wrapper.find('button.accordion-trigger').trigger('click')
        await flushPromises()
        await wrapper.findComponent(OAuthConnectFlow).find('button').trigger('click')
      }

      const ids = wrappers.map((w) => w.get('input[type="text"]').attributes('id'))
      expect(new Set(ids).size).toBe(wrappers.length)
      for (const id of ids) {
        expect(document.querySelectorAll(`#${id}`)).toHaveLength(1)
      }
      for (const wrapper of wrappers) wrapper.unmount()
    })

    it('announces the connect from a region the connect flow does not own', async () => {
      const { wrapper, store } = await expandGog(false)
      const region = wrapper.get('[data-testid="oauth-message"]').element

      // What a successful exchange leaves behind: the confirmation, then the
      // status flip that unmounts OAuthConnectFlow and its own message.
      store.oauthMessages['gog_work'] = 'GOG account connected successfully!'
      store.oauthStatus['gog_work'] = {
        enabled: true,
        connected: true,
        authUrl: null,
      }
      await flushPromises()

      expect(wrapper.findComponent(OAuthConnectFlow).exists()).toBe(false)
      const surviving = wrapper.get('[data-testid="oauth-message"]')
      expect(surviving.element).toBe(region)
      expect(surviving.text()).toContain('GOG account connected successfully!')
    })

    it('shows a connect message once, in the panel and not inside the flow', async () => {
      vi.spyOn(window, 'open').mockReturnValue(null)
      // The matching origin is what gets the code step — and the message site
      // that used to duplicate the panel's — onto the screen at all.
      const { wrapper, store } = await expandGog(false, 'https://login.gog.com/auth')
      await wrapper.findComponent(OAuthConnectFlow).get('button').trigger('click')
      expect(wrapper.findComponent(OAuthConnectFlow).find('input').exists()).toBe(true)

      store.oauthMessages['gog_work'] = 'Connecting to GOG...'
      await flushPromises()

      const region = wrapper.get('[data-testid="oauth-message"]')
      expect(region.text()).toBe('GOG (work): Connecting to GOG...')
      // Visible, not sr-only: with no other rendering site, a refused
      // disconnect reached screen readers and nobody else.
      expect(region.classes()).not.toContain('sr-only')
      expect(wrapper.findComponent(OAuthConnectFlow).text()).not.toContain(
        'Connecting to GOG',
      )
    })

    it('gives the focus target a visible line naming the connected account', async () => {
      const { wrapper } = await expandGog(true)

      // GOG and Epic had no connected line at all, so focus landed on a group
      // with zero children: nothing on screen said where Tab resumes.
      const panel = wrapper.get('.source-accordion-oauth')
      const connected = wrapper.get('[data-testid="oauth-connected"]')
      expect(panel.element.contains(connected.element)).toBe(true)
      expect(connected.text()).toBe('GOG account connected.')
      // Not a live region: it is inserted already populated.
      expect(connected.attributes('role')).toBeUndefined()
    })

    it('names the source in the live region several panels announce into', async () => {
      const { wrapper, store } = await expandGog(false)
      const region = wrapper.get('[data-testid="oauth-message"]').element
      // Empty before, so the prefix cannot be read as standing page content.
      expect(region.textContent).toBe('')

      store.oauthMessages['gog_work'] = 'Connecting to GOG...'
      await flushPromises()

      // Same node throughout — a re-created region is skipped entirely.
      expect(wrapper.get('[data-testid="oauth-message"]').element).toBe(region)
      expect(region.textContent).toBe('GOG (work): Connecting to GOG...')
      // Nothing collapses the other accordions, so an announcement from a
      // panel the user is not standing in has to say which source it is.
      expect(
        wrapper.get('[data-testid="oauth-message"] .sr-only').text(),
      ).toBe('GOG (work):')
    })

    it('lands focus on the panel when a successful connect unmounts Submit', async () => {
      vi.spyOn(window, 'open').mockReturnValue(null)
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: gogSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, gogConfig, {
        enabled: true,
        connected: false,
        authUrl: 'https://login.gog.com/auth',
      })
      vi.spyOn(store, 'submitGogCode').mockImplementation(async (id: string) => {
        store.oauthStatus[id] = { enabled: true, connected: true, authUrl: null }
      })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const flow = wrapper.findComponent(OAuthConnectFlow)
      await flow.get('button').trigger('click')
      await flow.get('input').setValue('auth-code')
      const submit = flow.findAll('button')[1].element as HTMLElement
      submit.focus()

      await flow.findAll('button')[1].trigger('click')
      await flushPromises()

      // The connect unmounts the whole flow, submit button included — the
      // mirror of the disconnect case, and the same drop to <body>.
      expect(wrapper.findComponent(OAuthConnectFlow).exists()).toBe(false)
      expect(document.activeElement).toBe(
        wrapper.get('.source-accordion-oauth').element,
      )
      wrapper.unmount()
    })

    it('leaves focus on Disconnect when the disconnect is refused', async () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: gogSource, syncing: false },
        attachTo: document.body,
      })
      const store = useDataStore()
      primeStore(store, gogConfig, {
        enabled: true,
        connected: true,
        authUrl: null,
      })
      // disconnectOAuth swallows the refusal, so the button and the connection
      // are both still there when it returns.
      vi.spyOn(store, 'disconnectGog').mockImplementation(async (id: string) => {
        store.oauthMessages[id] = 'Error: GOG is not enabled for that source.'
      })

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      const disconnect = wrapper.get('[data-testid="disconnect-btn-gog_work"]')
      ;(disconnect.element as HTMLElement).focus()
      await disconnect.trigger('click')
      await flushPromises()

      // Moving focus unconditionally parked it on a panel with no children.
      expect(document.activeElement).toBe(disconnect.element)
      const region = wrapper.get('[data-testid="oauth-message"]')
      expect(region.text()).toContain('not enabled for that source')
      expect(region.classes()).not.toContain('sr-only')
      wrapper.unmount()
    })

    it('offers a disabled Connect and the remedy when there is no auth URL', async () => {
      // What the server sends for a source it will not connect. Gating the
      // whole connect block on the auth URL left the named group with no
      // children: "GOG (work) connection, grouping" and nothing inside it.
      const { wrapper } = await expandGog(false, null)

      const connect = wrapper.findComponent(OAuthConnectFlow).get('button')
      expect(connect.attributes('aria-disabled')).toBe('true')
      const hint = wrapper.get('[data-testid="oauth-connect-hint"]')
      expect(hint.text()).toContain('sign-in link')
      expect(connect.attributes('aria-describedby')).toBe(hint.attributes('id'))
      expect(
        wrapper.get('.source-accordion-oauth').element.contains(hint.element),
      ).toBe(true)
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

    it('reports the status as unknown when a connect cannot be re-read', async () => {
      vi.spyOn(window, 'open').mockReturnValue(null)
      const { wrapper, store } = await expandGog(false, 'https://login.gog.com/auth')
      // The token is stored and the confirmation is out; the re-read failed.
      vi.spyOn(store, 'submitGogCode').mockImplementation(async (id: string) => {
        store.oauthMessages[id] = 'GOG account connected successfully!'
        throw new Error('status read failed')
      })

      const flow = wrapper.findComponent(OAuthConnectFlow)
      await flow.get('button').trigger('click')
      await flow.get('input').setValue('auth-code')
      await flow.findAll('button')[1].trigger('click')
      await flushPromises()

      // The stale flag reads as "not connected", so the panel kept offering
      // Connect for an account that is now connected.
      expect(wrapper.findComponent(OAuthConnectFlow).exists()).toBe(false)
      expect(wrapper.get('[data-testid="oauth-status-error"]').text()).toContain(
        'Could not read',
      )
    })

    it('clears a stale message before the panel is shown again', async () => {
      const { wrapper, store } = await expandGog(true)
      store.oauthMessages['gog_work'] = 'Disconnected. You can reconnect below.'
      await flushPromises()
      expect(wrapper.get('[data-testid="oauth-message"]').text()).not.toBe('')

      await wrapper.find('button.accordion-trigger').trigger('click')
      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()

      // Accordion.vue hides the body with `hidden` rather than unmounting it,
      // so re-expanding puts the region back in the accessibility tree — and a
      // populated one is read as page content, not as a status.
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toBe('')
    })

    // The Epic branch is `v-else-if="isEpic"`, so a third OAuth plugin cannot
    // silently inherit Epic's flow, origin and label.
    const epicSource = {
      id: 'epic_work',
      display_name: 'Epic (work)',
      plugin_display_name: 'Epic Games',
      enabled: true,
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

    it('Epic disconnect names the source and labels itself Epic', async () => {
      const { wrapper, store } = await expandEpic(true)
      const disconnect = vi
        .spyOn(store, 'disconnectEpic')
        .mockResolvedValue(undefined)

      const button = wrapper.find('[data-testid="disconnect-btn-epic_work"]')
      expect(button.attributes('aria-label')).toBe(
        'Disconnect Epic (work) from Epic Games',
      )
      await button.trigger('click')

      expect(disconnect).toHaveBeenCalledWith('epic_work')
    })
  })

  // Neither flow can work out its own remedy. Trakt's `enabled` is false both
  // for a disabled source and for one missing client credentials, and the auth
  // URL is null both for a disabled source and for a builder that threw.
  describe('the hint under a disabled Connect', () => {
    async function expand(
      plugin: string,
      sourceEnabled: boolean,
      oauth: OAuthStatus,
    ) {
      const id = `${plugin}_work`
      const wrapper = mount(SyncSourceAccordion, {
        props: {
          source: {
            id,
            display_name: id,
            plugin_display_name: plugin,
            enabled: sourceEnabled,
          },
          syncing: false,
        },
      })
      const store = useDataStore()
      const { loadOAuthStatus } = primeStore(
        store,
        { ...migratedConfig, source_id: id, plugin, enabled: sourceEnabled },
        oauth,
      )

      await wrapper.find('button.accordion-trigger').trigger('click')
      await flushPromises()
      return { wrapper, store, loadOAuthStatus }
    }

    /** Make ``setSourceEnabled`` land on the config the way the store does. */
    function stubEnableToggle(store: ReturnType<typeof useDataStore>) {
      return vi
        .spyOn(store, 'setSourceEnabled')
        .mockImplementation(async (id: string, enabled: boolean) => {
          store.sourceConfigs[id] = { ...store.sourceConfigs[id], enabled }
        })
    }

    it('tells a disabled Trakt source to enable itself', async () => {
      // One click from connectable, and it was being told to add the client
      // credentials it already has.
      const { wrapper } = await expand('trakt', false, UNCONNECTABLE)

      expect(wrapper.get('[data-testid="trakt-connect-hint"]').text()).toBe(
        'Enable this source in the settings below before you can connect.',
      )
    })

    it('tells an enabled Trakt source to add its client credentials', async () => {
      const { wrapper } = await expand('trakt', true, UNCONNECTABLE)

      expect(wrapper.get('[data-testid="trakt-connect-hint"]').text()).toBe(
        'Add the Trakt client ID and client secret in the settings below ' +
          'before you can connect.',
      )
    })

    it('does not tell an enabled Epic source to enable itself', async () => {
      const { wrapper } = await expand('epic_games', true, {
        ...UNCONNECTABLE,
        enabled: true,
      })

      expect(wrapper.get('[data-testid="oauth-connect-hint"]').text()).toBe(
        'The service did not return a sign-in link. Try again in a moment.',
      )
    })

    it('tells a disabled GOG source to enable itself', async () => {
      const { wrapper } = await expand('gog', false, UNCONNECTABLE)

      expect(wrapper.get('[data-testid="oauth-connect-hint"]').text()).toBe(
        'Enable this source in the settings below before you can connect.',
      )
    })

    it('does not name the credentials remedy while the status is stale after an enable', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        false,
        UNCONNECTABLE,
      )
      stubEnableToggle(store)
      loadOAuthStatus.mockClear()
      // Held open so the window where only the config half has moved is
      // observable — the window the hint used to be read in.
      let releaseStatus: () => void = () => {}
      loadOAuthStatus.mockImplementation(
        () =>
          new Promise<void>((resolve) => {
            releaseStatus = () => {
              store.oauthStatus['trakt_work'] = {
                ...UNCONNECTABLE,
                enabled: true,
              }
              resolve()
            }
          }),
      )

      await wrapper.get('[data-testid="form-toggle-enabled"]').trigger('click')
      await flushPromises()

      // config.enabled is true here and the status is still the disabled read,
      // so the hint landed on "add the Trakt client ID and client secret" —
      // told to a source that has them, beside a button nothing would revive.
      expect(wrapper.get('[data-testid="trakt-connect-hint"]').text()).toBe(
        'Rechecking the connection status…',
      )

      releaseStatus()
      await flushPromises()

      expect(loadOAuthStatus).toHaveBeenCalledWith('trakt_work', 'trakt')
      expect(wrapper.find('[data-testid="trakt-connect-hint"]').exists()).toBe(
        false,
      )
      expect(
        wrapper
          .get('[data-testid="trakt-connect-btn"]')
          .attributes('aria-disabled'),
      ).toBeUndefined()
      // The toggle blurs to <body> while it is busy, so the region is the only
      // thing that can report what the recheck found.
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'Connection status updated.',
      )
    })

    it('keeps the recheck open when a second write overtakes the first', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        false,
        UNCONNECTABLE,
      )
      stubEnableToggle(store)
      vi.spyOn(store, 'clearSourceSecret').mockResolvedValue(undefined)
      const answer: Array<() => void> = []
      loadOAuthStatus.mockImplementation(
        () =>
          new Promise<void>((resolve) => {
            answer.push(resolve)
          }),
      )

      // The secret buttons gate on `syncing` alone, so Clear stays live all
      // the way through the enable: two rechecks, in flight together.
      await wrapper.get('[data-testid="form-toggle-enabled"]').trigger('click')
      await flushPromises()
      await wrapper.get('[data-testid="secret-clear-api_key"]').trigger('click')
      await flushPromises()
      expect(answer).toHaveLength(2)

      // The enable's read answers first, against a gate the clear has already
      // moved again — the store drops its payload for the same reason.
      answer[0]()
      await flushPromises()

      // Stamping that outcome dropped the hint out of "Rechecking…" and named
      // a remedy worked out from half-applied state.
      expect(wrapper.get('[data-testid="trakt-connect-hint"]').text()).toBe(
        'Rechecking the connection status…',
      )
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'Rechecking the connection status…',
      )

      store.oauthStatus['trakt_work'] = { ...UNCONNECTABLE, enabled: true }
      answer[1]()
      await flushPromises()

      // Only the last recheck standing gets to say what the gate now is.
      expect(wrapper.find('[data-testid="trakt-connect-hint"]').exists()).toBe(
        false,
      )
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'Connection status updated.',
      )
    })

    it('rechecks the gate after either secret verb', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        true,
        UNCONNECTABLE,
      )
      vi.spyOn(store, 'setSourceSecret').mockResolvedValue(undefined)
      vi.spyOn(store, 'clearSourceSecret').mockResolvedValue(undefined)
      loadOAuthStatus.mockClear()

      // Trakt's `enabled` folds in whether the client credentials resolve, so
      // storing one moves the gate exactly as the enable toggle does.
      await wrapper.get('[data-testid="secret-replace-api_key"]').trigger('click')
      await wrapper.get('#secret-input-api_key').setValue('fresh')
      await wrapper.get('[data-testid="secret-save-api_key"]').trigger('click')
      await flushPromises()

      expect(loadOAuthStatus).toHaveBeenCalledTimes(1)

      await wrapper.get('[data-testid="secret-clear-api_key"]').trigger('click')
      await flushPromises()

      expect(loadOAuthStatus).toHaveBeenCalledTimes(2)
    })

    it('rechecks the gate after a settings save', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        true,
        UNCONNECTABLE,
      )
      vi.spyOn(store, 'updateSourceConfig').mockResolvedValue(undefined)
      loadOAuthStatus.mockClear()

      // Trakt's client ID is an ordinary field on this form, so a save moves
      // the same gate the secret verbs do.
      await wrapper.get('input[name="vanity_url"]').setValue('cid')
      await wrapper.get('[data-testid="form-save"]').trigger('click')
      await flushPromises()

      expect(loadOAuthStatus).toHaveBeenCalledTimes(1)
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'Connection status updated.',
      )
    })

    it('leaves the gate unread when the save was refused', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        true,
        UNCONNECTABLE,
      )
      vi.spyOn(store, 'updateSourceConfig').mockRejectedValue(
        new Error('save blew up'),
      )
      loadOAuthStatus.mockClear()

      await wrapper.get('[data-testid="form-save"]').trigger('click')
      await flushPromises()

      // Nothing was written, so announcing a fresh status would report a
      // recheck of a gate that never moved, over the error the user needs.
      expect(loadOAuthStatus).not.toHaveBeenCalled()
      expect(wrapper.get('[data-testid="form-save-status"]').text()).toContain(
        'save blew up',
      )
    })

    it('says so when the recheck after a write cannot be read', async () => {
      const { wrapper, store, loadOAuthStatus } = await expand(
        'trakt',
        false,
        UNCONNECTABLE,
      )
      stubEnableToggle(store)
      loadOAuthStatus.mockRejectedValueOnce(new Error('status read failed'))

      await wrapper.get('[data-testid="form-toggle-enabled"]').trigger('click')
      await flushPromises()

      // The connect flow is swapped for the error and its Retry, and neither
      // announces: without the region the write's outcome reaches nobody.
      expect(wrapper.get('[data-testid="oauth-message"]').text()).toContain(
        'Could not read the connection status. Try again in a moment.',
      )
      expect(wrapper.find('[data-testid="oauth-status-retry"]').exists()).toBe(
        true,
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
        errors: [] as SyncErrorResponse[],
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

    it('renders each error message for a completed job', () => {
      const job = makeJob({
        status: 'completed',
        errors: [
          { source: 'Steam', message: 'Set verify_ssl to false' },
          { source: 'Steam', message: "Failed to process 'Portal 2'" },
        ],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const errors = wrapper.get('[data-testid="source-sync-errors"]')
      expect(errors.findAll('li').map((li) => li.text())).toEqual([
        'Set verify_ssl to false',
        "Failed to process 'Portal 2'",
      ])
    })

    it('says in text that the list is a list of errors', () => {
      const job = makeJob({
        status: 'completed',
        errors: [{ source: 'Steam', message: 'Set verify_ssl to false' }],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      // A red tint was the only thing saying these were failures, at 1.16:1
      // against the card — and remedy text ("Set verify_ssl to false") reads as
      // a suggestion on its own (WCAG 1.4.1).
      const title = wrapper.get('[data-testid="source-sync-errors-title"]')
      expect(title.text()).toBe('Last sync errors for Steam')
      expect(title.classes()).not.toContain('sr-only')
      const errors = wrapper.get('[data-testid="source-sync-errors"]')
      // Named by the visible title, not a parallel aria-label: list names are
      // announced inconsistently in linear reading, which left the source
      // attribution resting on DOM position alone.
      expect(errors.attributes('aria-labelledby')).toBe(title.attributes('id'))
      expect(errors.attributes('aria-label')).toBeUndefined()
    })

    it('caps the rendered messages and counts the rest', () => {
      // One entry per failed item, so a Calibre library failing per-item
      // rendered thousands of rows and pushed the tab off screen.
      const job = makeJob({
        status: 'completed',
        errors: Array.from({ length: 7 }, (_, i) => ({
          source: 'Steam',
          message: `Item ${i} failed`,
        })),
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      const items = wrapper
        .get('[data-testid="source-sync-errors"]')
        .findAll('li')
        .map((li) => li.text())
      expect(items).toEqual([
        'Item 0 failed',
        'Item 1 failed',
        'Item 2 failed',
        'Item 3 failed',
        'Item 4 failed',
        '… and 2 more',
      ])
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

    it('shows the messages without expanding the accordion', () => {
      const job = makeJob({
        status: 'completed',
        errors: [{ source: 'Steam', message: 'Set verify_ssl to false' }],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job },
      })

      // Outside the collapsible panel, and outside the trigger button whose
      // accessible name would otherwise swallow the whole message.
      const errors = wrapper.get('[data-testid="source-sync-errors"]')
      const panel = wrapper.get('.accordion-panel')
      const trigger = wrapper.get('button.accordion-trigger')
      expect(panel.attributes('hidden')).toBeDefined()
      expect(panel.element.contains(errors.element)).toBe(false)
      expect(trigger.element.contains(errors.element)).toBe(false)
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

    it('hides the errors while a sync is in progress', () => {
      const job = makeJob({
        status: 'running',
        errors: [{ source: 'Steam', message: 'Rate limit exceeded' }],
      })
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: true, job },
      })

      expect(wrapper.find('[data-testid="source-sync-errors"]').exists()).toBe(
        false,
      )
    })

    it('renders nothing extra when job is null', () => {
      const wrapper = mount(SyncSourceAccordion, {
        props: { source: baseSource, syncing: false, job: null },
      })

      // The aria-live progress region is in the DOM via v-show but
      // hidden, and the error list is absent because there's no job.
      expect(wrapper.find('[data-testid="source-sync-errors"]').exists()).toBe(
        false,
      )
      const region = wrapper.find('.source-accordion-progress')
      expect(region.exists()).toBe(true)
      expect((region.element as HTMLElement).style.display).toBe('none')
    })
  })
})
