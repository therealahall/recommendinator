import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import LibraryPage from './LibraryPage.vue'
import { useLibraryStore } from '@/stores/library'

// jsdom has no IntersectionObserver; the page sets one up on mount.
class FakeIntersectionObserver {
  observe = vi.fn()
  disconnect = vi.fn()
  unobserve = vi.fn()
  takeRecords = vi.fn(() => [])
}

beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
})

function mountPage(overrides: Record<string, unknown> = {}) {
  const wrapper = mount(LibraryPage, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
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
  it('renders the search empty state with the query and a Clear search button', async () => {
    const { wrapper } = mountPage({ items: [], loading: false, searchQuery: 'dune' })
    await wrapper.vm.$nextTick()

    const empty = wrapper.find('.empty-state-search')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('dune')
    const clearBtn = empty.findAll('button').find((b) => b.text() === 'Clear search')
    expect(clearBtn).toBeDefined()
  })

  it('clicking Clear search calls setFilter with an empty search', async () => {
    const { wrapper, lib } = mountPage({ items: [], loading: false, searchQuery: 'dune' })
    await wrapper.vm.$nextTick()

    const clearBtn = wrapper.find('.empty-state-search').findAll('button').find((b) => b.text() === 'Clear search')!
    await clearBtn.trigger('click')

    expect(lib.setFilter).toHaveBeenCalledWith('search', '')
  })

  it('reflects searchAnnouncement in the polite live region', async () => {
    const { wrapper } = mountPage({ searchAnnouncement: '2 items match “dune”' })
    await wrapper.vm.$nextTick()

    const region = wrapper.find('[role="status"]')
    expect(region.exists()).toBe(true)
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('2 items match “dune”')
  })

  it('renders the generic empty state when there is no search query', async () => {
    const { wrapper } = mountPage({ items: [], loading: false, searchQuery: '' })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.empty-state-search').exists()).toBe(false)
    const generic = wrapper.find('.empty-state')
    expect(generic.exists()).toBe(true)
    expect(generic.text()).toContain('No items found')
  })
})
