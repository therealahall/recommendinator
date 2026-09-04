import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DataPage from './DataPage.vue'
import ImportPanel from '@/components/organisms/ImportPanel.vue'
import { useDataStore } from '@/stores/data'
import type { SyncJobResponse } from '@/types/api'

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
  sync_interval: 'daily',
  last_run_at: null,
  last_run_status: null,
  next_run_at: null,
}

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
        omitted_errors: 0,
        items_added: 5,
        items_updated: 1,
        items_unchanged: 1,
      },
    ],
  }
}

describe('DataPage rows during a Sync All', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockPut.mockReset()
  })

  it('locks the Sync button of a source the run has not reached yet', async () => {
    const goodreads = {
      ...enabledSource,
      id: 'goodreads',
      display_name: 'Goodreads',
      plugin_display_name: 'Goodreads',
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
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true, ImportPanel: true } },
    })
    await flushPromises()

    await wrapper.get('.sync-all-card button').trigger('click')
    await flushPromises()
    jobs = [allSourcesRunning()]
    await useDataStore().checkSyncStatus()
    await wrapper.vm.$nextTick()

    const button = wrapper.get('[data-testid="sync-btn-goodreads"]')
    expect(button.text()).toBe('Syncing…')
    expect(button.attributes('aria-disabled')).toBe('true')
    await button.trigger('click')
    expect(mockPost.mock.calls.filter(([path]) => path === '/update')).toEqual([
      ['/update', { source: 'all' }],
    ])
    wrapper.unmount()
  })

  it('reaches importing a file from the Data page', async () => {
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') return Promise.resolve([enabledSource])
      return Promise.resolve({})
    })
    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
    })
    await flushPromises()

    expect(wrapper.findComponent(ImportPanel).exists()).toBe(true)
    wrapper.unmount()
  })

  it('refuses a Sync All while a per-source run nobody triggered here is in flight', async () => {
    const goodreads = {
      ...enabledSource,
      id: 'goodreads',
      display_name: 'Goodreads',
      plugin_display_name: 'Goodreads',
    }
    const steamJob: SyncJobResponse = {
      ...allSourcesRunning(),
      source: 'Steam',
      sources: [],
    }
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') {
        return Promise.resolve([enabledSource, goodreads])
      }
      if (path === '/sync/status') {
        return Promise.resolve({ status: 'running', jobs: [steamJob] })
      }
      if (path === '/enrichment/status') {
        return Promise.resolve({ running: false, completed: false })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true, ImportPanel: true } },
    })
    await flushPromises()

    const button = wrapper.get('.sync-all-card button')
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('aria-label')).toBe(
      'Sync all sources — another sync is in progress',
    )
    await button.trigger('click')
    expect(mockPost.mock.calls.filter(([path]) => path === '/update')).toEqual([])
    wrapper.unmount()
  })

  it('keeps focus on Sync All for the whole run instead of dropping it to <body>', async () => {
    const goodreads = {
      ...enabledSource,
      id: 'goodreads',
      display_name: 'Goodreads',
      plugin_display_name: 'Goodreads',
    }
    mockPost.mockImplementation((path: string) =>
      path === '/update' ? new Promise(() => {}) : Promise.resolve({}),
    )
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') return Promise.resolve([enabledSource, goodreads])
      if (path === '/sync/status') return Promise.resolve({ status: 'idle', jobs: [] })
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true, ImportPanel: true } },
      attachTo: document.body,
    })
    await flushPromises()

    const button = wrapper.get('.sync-all-card button')
    ;(button.element as HTMLButtonElement).focus()
    await button.trigger('click')
    await flushPromises()

    expect(button.attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(button.element)
    await button.trigger('click')
    expect(mockPost.mock.calls.filter(([path]) => path === '/update')).toHaveLength(1)
    wrapper.unmount()
  })

  it('lands the keyboard in the sources panel when the first source unmounts Add', async () => {
    let sources: unknown[] = []
    mockPost.mockResolvedValue({})
    mockGet.mockImplementation((path: string) => {
      if (path === '/sync/sources') return Promise.resolve(sources)
      return Promise.resolve({})
    })

    const wrapper = mount(DataPage, {
      global: { stubs: { AddSourceModal: true, EnrichmentCard: true, ImportPanel: true } },
      attachTo: document.body,
    })
    await flushPromises()

    const add = wrapper.get('[data-testid="add-first-source-btn"]')
    ;(add.element as HTMLButtonElement).focus()
    await add.trigger('click')
    sources = [enabledSource]
    await useDataStore().loadSyncSources()
    wrapper.findComponent({ name: 'AddSourceModal' }).vm.$emit('created', 'steam')
    await flushPromises()

    expect(wrapper.find('[data-testid="add-first-source-btn"]').exists()).toBe(false)
    expect(document.activeElement).toBe(
      wrapper.get('[data-testid="sync-sources-panel"]').element,
    )
    wrapper.unmount()
  })

  describe('DataPage retry outcome', () => {
    async function mountFailed(retrySucceeds: boolean) {
      mockPost.mockResolvedValue({})
      let sourcesFail = true
      mockGet.mockImplementation((path: string) => {
        if (path !== '/sync/sources') return Promise.resolve({})
        if (sourcesFail) return Promise.reject(new Error('boom'))
        return Promise.resolve([enabledSource])
      })

      const wrapper = mount(DataPage, {
        global: { stubs: { AddSourceModal: true, EnrichmentCard: true, ImportPanel: true } },
        attachTo: document.body,
      })
      await flushPromises()
      sourcesFail = !retrySucceeds
      return wrapper
    }

    it('announces the reload and moves focus off the unmounted Retry', async () => {
      const wrapper = await mountFailed(true)

      const retry = wrapper.get('[data-testid="sync-sources-retry"]')
      ;(retry.element as HTMLButtonElement).focus()
      await retry.trigger('click')
      await flushPromises()

      expect(wrapper.get('[data-testid="sync-sources-retry-status"]').text()).toBe(
        'Sync sources loaded.',
      )
      const panel = wrapper.get('[data-testid="sync-sources-panel"]')
      expect(document.activeElement).toBe(panel.element)
      wrapper.unmount()
    })

  })
})
