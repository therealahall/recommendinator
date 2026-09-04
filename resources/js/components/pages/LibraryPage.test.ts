import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import { createRouter, createMemoryHistory } from 'vue-router'
import LibraryPage from './LibraryPage.vue'
import EditModal from '@/components/molecules/EditModal.vue'
import LoadingRows from '@/components/molecules/LoadingRows.vue'
import { useLibraryStore } from '@/stores/library'
import { useDataStore } from '@/stores/data'

class FakeIntersectionObserver {
  observe = vi.fn()
  disconnect = vi.fn()
  unobserve = vi.fn()
  takeRecords = vi.fn(() => [])
}

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
})

function testRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/library', name: 'library', component: { template: '<div />' } },
      { path: '/library/duplicates', name: 'duplicates', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
    ],
  })
}

function mountPage(overrides: Record<string, unknown> = {}, attachTo?: HTMLElement) {
  const wrapper = mount(LibraryPage, {
    attachTo,
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn }), testRouter()],
      stubs: {
        LibraryFilters: true,
        LibraryCard: true,
        EditModal: true,
      },
    },
  })
  const lib = useLibraryStore()
  Object.assign(lib, { items: [], loading: false, searchQuery: '', searchAnnouncement: '', error: '', ...overrides })
  return { wrapper, lib }
}

describe('LibraryPage search behaviour', () => {
  it('offers the only route to duplicates review', async () => {
    const { wrapper } = mountPage()
    await wrapper.vm.$nextTick()

    const entry = wrapper.findAll('a').find((link) => link.text().includes('duplicates'))
    expect(entry?.attributes('href')).toBe('/library/duplicates')
  })

  it('fills the page with placeholders while it loads, and flags itself busy', async () => {
    const { wrapper } = mountPage({ items: [], loading: true })
    await wrapper.vm.$nextTick()

    const placeholders = wrapper.findComponent(LoadingRows)
    expect(placeholders.exists()).toBe(true)
    expect(placeholders.attributes('aria-hidden')).toBe('true')
    expect(wrapper.attributes('aria-busy')).toBe('true')
  })

  it('names the query that matched nothing, so the way out is on screen', async () => {
    const { wrapper } = mountPage({ items: [], loading: false, searchQuery: 'dune' })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('dune')
    expect(wrapper.findAll('button').some((b) => b.text() === 'Clear search')).toBe(true)
  })

  it('clicking Clear search calls setFilter with an empty search', async () => {
    const { wrapper, lib } = mountPage({ items: [], loading: false, searchQuery: 'dune' })
    await wrapper.vm.$nextTick()

    const clearBtn = wrapper.findAll('button').find((b) => b.text() === 'Clear search')!
    await clearBtn.trigger('click')

    expect(lib.setFilter).toHaveBeenCalledWith('search', '')
  })

  it('reflects searchAnnouncement in the polite live region', async () => {
    const { wrapper } = mountPage({
      items: [
        { db_id: 1, title: 'Dune', content_type: 'book', status: 'unread' },
        { db_id: 2, title: 'Dune Messiah', content_type: 'book', status: 'unread' },
      ],
      searchAnnouncement: '2 items match “dune”',
    })
    await wrapper.vm.$nextTick()

    const region = wrapper.find('[role="status"]')
    expect(region.exists()).toBe(true)
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('2 items match “dune”')
  })

  it('speaks the load, then the count that landed, with no query to describe', async () => {
    const { wrapper, lib } = mountPage({ items: [], loading: true })
    await wrapper.vm.$nextTick()
    const region = () => wrapper.get('[role="status"]')
    const whileLoading = region().text()

    expect(whileLoading).not.toBe('')

    Object.assign(lib, {
      loading: false,
      hasMore: false,
      items: [{ db_id: 1, title: 'Dune', content_type: 'book', status: 'unread' }],
    })
    await wrapper.vm.$nextTick()

    expect(region().text()).not.toBe(whileLoading)
    expect(region().text()).toContain('1')
  })

  it('hands a refused save to the dialog, and never to the load banner', async () => {
    const refusal = 'Review must be at most 10000 characters.'
    const { wrapper } = mountPage({
      editingItem: { db_id: 1, title: 'Dune', content_type: 'book', status: 'unread' },
      editError: refusal,
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).not.toContain('Failed to load library')
    expect(wrapper.findComponent(EditModal).props('saveError')).toBe(refusal)
  })

  it('restores an item to automatic enrichment and reloads what the dialog shows', async () => {
    const { wrapper, lib } = mountPage({
      editingItem: { db_id: 1, title: 'Dune', content_type: 'book', status: 'unread' },
    })
    const data = useDataStore()
    await wrapper.vm.$nextTick()

    wrapper.findComponent(EditModal).vm.$emit('restoreEnrichment', 1)
    await flushPromises()

    expect(data.restoreItemEnrichment).toHaveBeenCalledWith(1)
    expect(lib.openEdit).toHaveBeenCalledWith(1)
  })

  it('clears the last refusal before restoring, so a second failure is announced', async () => {
    const { wrapper, lib } = mountPage({
      editingItem: { db_id: 1, title: 'Dune', content_type: 'book', status: 'unread' },
      editError: 'Failed to restore enrichment',
    })
    const data = useDataStore()
    vi.mocked(data.restoreItemEnrichment).mockRejectedValue(new Error('still down'))
    await wrapper.vm.$nextTick()

    wrapper.findComponent(EditModal).vm.$emit('restoreEnrichment', 1)

    expect(lib.editError).toBe('')
    await flushPromises()
    expect(lib.editError).toBe('still down')
  })

  it('renders the generic empty state when there is no search query', async () => {
    const { wrapper } = mountPage({ items: [], loading: false, searchQuery: '' })
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('button').some((b) => b.text() === 'Clear search')).toBe(false)
    expect(wrapper.find('[data-testid="library-empty"]').exists()).toBe(true)
  })

  it('offers to widen a filter that emptied the list, not to go and sync', async () => {
    const { wrapper, lib } = mountPage({ items: [], loading: false, statusFilter: 'completed' })
    await wrapper.vm.$nextTick()

    await wrapper.get('[data-testid="library-clear-filters"]').trigger('click')

    expect(lib.clearFilters).toHaveBeenCalled()
  })

  it('sends an empty library to the sources that would fill it', async () => {
    const { wrapper } = mountPage({ items: [], loading: false })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="library-clear-filters"]').exists()).toBe(false)
    expect(
      wrapper.findAll('a').some((link) => link.attributes('href') === '/data'),
    ).toBe(true)
  })

  const EMPTIED_BY: Array<
    [string, Record<string, unknown>, (lib: ReturnType<typeof useLibraryStore>) => void]
  > = [
    [
      'library-clear-search',
      { searchQuery: 'dune' },
      (lib) =>
        vi.mocked(lib.setFilter).mockImplementation(() => {
          lib.searchQuery = ''
          return undefined
        }),
    ],
    [
      'library-clear-filters',
      { statusFilter: 'completed' },
      (lib) =>
        vi.mocked(lib.clearFilters).mockImplementation(async () => {
          lib.statusFilter = ''
        }),
    ],
  ]

  it.each(EMPTIED_BY)(
    'lands the keyboard on the heading when %s destroys its own block',
    async (testid, state, settle) => {
      const { wrapper, lib } = mountPage({ items: [], loading: false, ...state }, document.body)
      await wrapper.vm.$nextTick()
      settle(lib)
      const action = wrapper.get(`[data-testid="${testid}"]`)
      ;(action.element as HTMLElement).focus()

      await action.trigger('click')
      await flushPromises()

      expect(wrapper.find(`[data-testid="${testid}"]`).exists()).toBe(false)
      expect(document.activeElement).toBe(wrapper.get('h2').element)
      wrapper.unmount()
    },
  )

  it('still sends an empty library to sync when Show ignored is on', async () => {
    const { wrapper } = mountPage({ items: [], loading: false, showIgnored: true })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="library-clear-filters"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="library-empty"]').exists()).toBe(true)
  })
})
