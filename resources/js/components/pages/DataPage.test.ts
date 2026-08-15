import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DataPage from './DataPage.vue'
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
}

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

// The page against the real store and a faked transport, so the row is judged
// on what it renders rather than on the prop it was handed.
describe('DataPage rows during a Sync All', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockPut.mockReset()
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

  /**
   * Symptom: Retry had no perceivable outcome. Success unmounted the focused
   * button silently; a repeat failure left the alert text unchanged. Root
   * cause: no live region, no focus fallback. Fix: a mounted role="status"
   * region and a panel to focus.
   */
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
        global: { stubs: { AddSourceModal: true, EnrichmentCard: true } },
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
