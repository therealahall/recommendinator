import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import { setActivePinia, createPinia } from 'pinia'
import DataPage from './DataPage.vue'
import SyncSourceAccordion from '@/components/organisms/SyncSourceAccordion.vue'
import { useDataStore } from '@/stores/data'
import type {
  SourceConfigResponse,
  SourceSchemaResponse,
  SyncJobResponse,
} from '@/types/api'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockPut = vi.fn()

vi.mock('@/composables/useApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useApi')>()),
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

const enabledSource = {
  id: 'steam',
  display_name: 'Steam',
  plugin_display_name: 'Steam',
  enabled: true,
  plugin_not_loaded: null,
}

const disabledSource = {
  id: 'roms',
  display_name: 'Roms',
  plugin_display_name: 'ROMs',
  enabled: false,
  plugin_not_loaded: null,
}

/** Mount with an "All Sources" run in flight and both sources listed. */
async function mountDuringSyncAll() {
  const wrapper = mount(DataPage, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      stubs: {
        SyncSourceAccordion: true,
        AddSourceModal: true,
        EnrichmentCard: true,
      },
    },
  })
  const data = useDataStore()
  // The run is going and resolved to Steam alone, which is what the store
  // reports per source id.
  vi.mocked(data.isSourceIdSyncing).mockImplementation(
    (id) => id === 'all' || id === 'steam',
  )
  data.syncSources = [enabledSource, disabledSource]
  await wrapper.vm.$nextTick()

  const rows = wrapper.findAllComponents(SyncSourceAccordion)
  return (id: string) => rows.find((row) => row.props('source').id === id)!
}

describe('DataPage sync-all regression', () => {
  // #106: the syncing prop ORed in isSourceIdSyncing('all') with no enabled
  // guard, so a disabled row read "Syncing…", hid its last-run errors and
  // locked its config form for the whole run — the server never syncs it.
  it('leaves a disabled source not syncing while All Sources runs', async () => {
    const row = await mountDuringSyncAll()

    expect(row('roms').props('syncing')).toBe(false)
  })

  it('still marks an enabled source syncing while All Sources runs', async () => {
    const row = await mountDuringSyncAll()

    expect(row('steam').props('syncing')).toBe(true)
  })
})

/** The umbrella run, carrying a progress slot only for the source it syncs. */
function allSourcesRunning(): SyncJobResponse {
  return {
    source: 'All Sources',
    status: 'running',
    started_at: '2026-08-14T10:00:00',
    completed_at: null,
    items_processed: 7,
    total_items: 8,
    current_item: 'Portal 2',
    current_source: 'Steam',
    error_message: null,
    progress_percent: 87,
    items_added: 5,
    items_updated: 1,
    items_unchanged: 1,
    errors: [],
    sources: [
      {
        source: 'Steam',
        items_processed: 7,
        total_items: 8,
        current_item: 'Portal 2',
        progress_percent: 87,
        items_added: 5,
        items_updated: 1,
        items_unchanged: 1,
      },
    ],
  }
}

/** What the row was showing before Sync All: the source's own last run, which
 *  reported errors. SyncManager retains it after the user disables the source. */
function romsFailedEarlier(): SyncJobResponse {
  return {
    source: 'Roms',
    status: 'completed',
    started_at: '2026-08-14T09:00:00',
    completed_at: '2026-08-14T09:01:00',
    items_processed: 3,
    total_items: 4,
    current_item: null,
    current_source: null,
    error_message: null,
    progress_percent: 75,
    items_added: 3,
    items_updated: 0,
    items_unchanged: 0,
    errors: [{ source: 'Roms', message: 'Set verify_ssl to false' }],
    sources: [],
  }
}

const romsSchema: SourceSchemaResponse = {
  source_id: 'roms',
  plugin: 'roms',
  plugin_display_name: 'ROMs',
  fields: [
    {
      name: 'rom_path',
      field_type: 'str',
      required: false,
      default: '',
      description: '',
      sensitive: false,
    },
  ],
}

const romsConfig: SourceConfigResponse = {
  source_id: 'roms',
  plugin: 'roms',
  plugin_display_name: 'ROMs',
  enabled: false,
  migrated: true,
  migrated_at: '2026-05-03T00:00:00Z',
  field_values: { rom_path: 'library' },
  secret_status: {},
}

// The page against the real store and a faked transport, so the row is judged
// on what it renders rather than on the prop it was handed.
describe('DataPage rows during a Sync All', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockPut.mockReset()
  })

  async function mountPage(jobs: SyncJobResponse[]) {
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') {
        return Promise.resolve([enabledSource, disabledSource])
      }
      if (path === '/sync/status') return Promise.resolve({ status: 'running', jobs })
      if (path === '/sync/sources/roms/schema') return Promise.resolve(romsSchema)
      if (path === '/sync/sources/roms/config') return Promise.resolve(romsConfig)
      if (path === '/enrichment/status') {
        return Promise.resolve({ running: false, completed: false })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
    })
    await flushPromises()
    return wrapper
  }

  function rowFor(
    wrapper: Awaited<ReturnType<typeof mountPage>>,
    id: string,
  ) {
    return wrapper
      .findAllComponents(SyncSourceAccordion)
      .find((row) => row.props('source').id === id)!
  }

  it('reads Sync on the disabled row and Syncing… on the enabled one', async () => {
    const wrapper = await mountPage([allSourcesRunning()])

    const roms = wrapper.get('[data-testid="sync-btn-roms"]')
    expect(roms.text()).toBe('Sync')
    // Speech input says the visible word back, so the two have to agree.
    expect(roms.attributes('aria-label')).toBe('Sync Roms — source is disabled')
    expect(roms.attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="sync-btn-steam"]').text()).toBe('Syncing…')
    wrapper.unmount()
  })

  it('gives the disabled row no progress of the run it is not part of', async () => {
    const wrapper = await mountPage([allSourcesRunning()])

    // The umbrella job carries a slot per source it syncs, and the disabled
    // one has none — a bar on that row would be measuring Steam's work.
    expect(rowFor(wrapper, 'roms').find('[role="progressbar"]').exists()).toBe(
      false,
    )
    expect(
      rowFor(wrapper, 'steam').get('[role="progressbar"]').attributes('aria-valuenow'),
    ).toBe('87')
    wrapper.unmount()
  })

  it('keeps the disabled row editable, Enable toggle included', async () => {
    const wrapper = await mountPage([allSourcesRunning()])
    mockPut.mockResolvedValue({ ...romsConfig, enabled: true })

    const roms = rowFor(wrapper, 'roms')
    await roms.get('button.accordion-trigger').trigger('click')
    await flushPromises()

    expect(roms.get('input[name="rom_path"]').attributes('disabled')).toBeUndefined()
    expect(roms.get('[data-testid="form-save"]').attributes('disabled')).toBeUndefined()
    const toggle = roms.get('[data-testid="form-toggle-enabled"]')
    expect(toggle.text()).toBe('Enable')
    expect(toggle.attributes('disabled')).toBeUndefined()

    await toggle.trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/sync/sources/roms/enabled', {
      enabled: true,
    })
    wrapper.unmount()
  })

  // The run started without this source, so the enable the unlocked form
  // allows cannot have joined it.
  it('does not claim a sync for a source enabled mid-run', async () => {
    const wrapper = await mountPage([allSourcesRunning()])
    mockPut.mockResolvedValue({ ...romsConfig, enabled: true })

    const roms = rowFor(wrapper, 'roms')
    await roms.get('button.accordion-trigger').trigger('click')
    await flushPromises()
    await roms.get('[data-testid="form-toggle-enabled"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="sync-btn-roms"]').text()).toBe('Sync')
    expect(roms.get('[data-testid="form-toggle-enabled"]').attributes('disabled'))
      .toBeUndefined()
    wrapper.unmount()
  })

  /**
   * Symptom: a second Sync click duplicated a source the run had not reached,
   * saving it twice at once. Root cause: membership came from progress slots,
   * which appear only once a source starts. Fix: record what /update resolved.
   */
  it('locks the Sync button of a source the run has not reached yet', async () => {
    const goodreads = {
      id: 'goodreads',
      display_name: 'Goodreads',
      plugin_display_name: 'Goodreads',
      enabled: true,
      plugin_not_loaded: null,
    }
    mockPost.mockResolvedValue({
      message: 'Sync started for All Sources',
      sources: ['steam', 'goodreads'],
    })
    let jobs: SyncJobResponse[] = []
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') {
        return Promise.resolve([enabledSource, goodreads])
      }
      if (path === '/sync/status') return Promise.resolve({ status: 'running', jobs })
      if (path === '/enrichment/status') {
        return Promise.resolve({ running: false, completed: false })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
    })
    await flushPromises()

    await wrapper.get('.sync-all-card button').trigger('click')
    await flushPromises()
    // The run reaches Steam first, and the poll that reports it drops the
    // optimistic flag — leaving the resolved list as the only thing that
    // knows Goodreads is spoken for.
    jobs = [allSourcesRunning()]
    await useDataStore().checkSyncStatus()
    await wrapper.vm.$nextTick()

    const button = wrapper.get('[data-testid="sync-btn-goodreads"]')
    expect(button.text()).toBe('Syncing…')
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(mockPost.mock.calls.filter(([path]) => path === '/update')).toEqual([
      ['/update', { source: 'all' }],
    ])
    wrapper.unmount()
  })

  // Regression: clicking Retry swapped the error branch for the spinner, so
  // the button holding focus was unmounted and the keyboard user landed on
  // <body> (WCAG 2.4.3).
  it('keeps focus on Retry while the reload it started is in flight', async () => {
    mockPost.mockResolvedValue({})
    let releaseSources: (value: unknown[]) => void = () => {}
    let sourcesFail = true
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') {
        return sourcesFail
          ? Promise.reject(new Error('boom'))
          : new Promise((resolve) => {
              releaseSources = resolve
            })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
      attachTo: document.body,
    })
    await flushPromises()

    sourcesFail = false
    const retry = wrapper.get('[data-testid="sync-sources-retry"]')
    ;(retry.element as HTMLButtonElement).focus()
    await retry.trigger('click')
    await wrapper.vm.$nextTick()

    expect(retry.text()).toBe('Retrying…')
    expect(retry.attributes('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(retry.element)

    releaseSources([enabledSource])
    await flushPromises()

    // The retry state has to end with the request, or the page holds the
    // failure branch open over a list it has already loaded.
    expect(wrapper.find('[data-testid="sync-sources-retry"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="sync-btn-steam"]').text()).toBe('Sync')
    wrapper.unmount()
  })

  // Regression: a rejected /sync/sources emptied the list, so a request that
  // never landed rendered as configuration advice the user cannot act on.
  it('says the load failed rather than that nothing is configured', async () => {
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) =>
      path === '/sync/sources'
        ? Promise.reject(new Error('boom'))
        : Promise.resolve({}),
    )

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('No sync sources configured')
    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Couldn't load sync sources",
    )
    expect(wrapper.find('[data-testid="sync-sources-retry"]').attributes('type'))
      .toBe('button')
    wrapper.unmount()
  })

  // Regression: a source whose plugin module raised vanished from the page,
  // which then advised adding sources to config.yaml — where it already was.
  it('names the module and the reason a source cannot run', async () => {
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) =>
      path === '/sync/sources'
        ? Promise.resolve([
            {
              ...enabledSource,
              id: 'my_site',
              display_name: 'My Site',
              enabled: false,
              plugin_not_loaded: {
                plugin: 'personal_site',
                failures: [
                  {
                    module: 'personal_site_games',
                    reason: 'ImportError: no scraper module',
                  },
                ],
              },
            },
          ])
        : Promise.resolve({}),
    )

    const wrapper = mount(DataPage, {
      global: {
        stubs: {
          SyncSourceAccordion: true,
          AddSourceModal: true,
          EnrichmentCard: true,
        },
      },
    })
    await flushPromises()

    // list-style: none strips list semantics in WebKit/VoiceOver, and how many
    // modules failed is the point of the notice.
    const list = wrapper.get('.unusable-sources-list')
    expect(list.attributes('role')).toBe('list')
    expect(list.get('li ul').attributes('role')).toBe('list')

    const notice = wrapper.get('[data-testid="unusable-sources"]').text()
    expect(notice).toContain('personal_site')
    expect(notice).toContain('personal_site_games')
    expect(notice).toContain('ImportError: no scraper module')
    expect(wrapper.text()).not.toContain('No sync sources configured')
    // No accordion: its schema and config reads would 404 on expand.
    expect(wrapper.findAllComponents(SyncSourceAccordion)).toHaveLength(0)
    wrapper.unmount()
  })

  // Catches dropping the `syncSourcesError = ''` reset at the top of
  // loadSyncSources: the card would outlive the failure and Retry would look
  // dead, with the store's own error test still green.
  it('clears the error and lists the sources when Retry succeeds', async () => {
    mockPost.mockResolvedValue({})
    let sourcesFail = true
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') {
        return sourcesFail
          ? Promise.reject(new Error('boom'))
          : Promise.resolve([enabledSource])
      }
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
    })
    await flushPromises()

    sourcesFail = false
    await wrapper.get('[data-testid="sync-sources-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="sync-btn-steam"]').text()).toBe('Sync')
    wrapper.unmount()
  })

  // The newer umbrella job carries no errors for a source it never ran, so
  // handing the row that job would blank the failure it still has.
  it('keeps the disabled row showing the errors of its own last run', async () => {
    const wrapper = await mountPage([romsFailedEarlier(), allSourcesRunning()])

    const errors = rowFor(wrapper, 'roms').get('[data-testid="source-sync-errors"]')
    expect(errors.findAll('li').map((li) => li.text())).toEqual([
      'Set verify_ssl to false',
    ])
    wrapper.unmount()
  })
})
