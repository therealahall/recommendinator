import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import RecommendationsPage from './RecommendationsPage.vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import type { ContentItemResponse } from '@/types/api'

function makeFullItem(overrides: Partial<ContentItemResponse> = {}): ContentItemResponse {
  return {
    external_ids: [{ source: 'goodreads', external_id: 'test-1' }],
    db_id: 1,
    title: 'A',
    author: 'Author',
    content_type: 'book',
    status: 'unread',
    rating: null,
    review: null,
    source: 'goodreads',
    ignored: false,
    seasons_watched: null,
    total_seasons: null,
    enriched: true,
    genres: [],
    tags: [],
    description: null,
    ...overrides,
  }
}

const mockGet = vi.fn()
const mockPatch = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: vi.fn(),
  }),
}))

const stubs = {
  RecControls: true,
  RecScoreDetails: true,
  StarRating: true,
  SeasonChecklist: true,
}

describe('RecommendationsPage', () => {
  let wrapper: VueWrapper | null = null

  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPatch.mockReset()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  async function mountWithItems() {
    wrapper = mount(RecommendationsPage, { global: { stubs }, attachTo: document.body })
    const store = useRecommendationsStore()
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, reasoning: '', score_breakdown: {}, variety_penalty: 0 },
      { db_id: 2, title: 'B', score: 0.8, reasoning: '', score_breakdown: {}, variety_penalty: 0 },
    ])
    await store.fetch()
    await flushPromises()
    return { wrapper, store }
  }

  it('opens the edit modal when a card is marked complete', async () => {
    const { wrapper } = await mountWithItems()
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(true)
  })

  it('removes the card after the modal saves', async () => {
    const { wrapper, store } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    mockPatch.mockResolvedValue({})
    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    expect(store.items.length).toBe(1)
    expect(store.items[0].db_id).toBe(2)
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)
  })

  it('shows the error bar when opening the edit modal fails', async () => {
    // openEdit's detail GET can fail; the store sets error and the page must
    // render the error bar so the click is not a silent dead button.
    const { wrapper } = await mountWithItems()

    mockGet.mockRejectedValue(new Error('Not found'))
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    const errorBar = wrapper.find('.status-bar.error')
    expect(errorBar.exists()).toBe(true)
    expect(errorBar.text()).toBe('Failed to load recommendations: Not found')
  })

  it('renders the page error bar when a save fails and clears it on a successful retry', async () => {
    // End-to-end check of the reworked error contract through the page, not just
    // the store: a rejected PATCH surfaces the error bar (modal still open, card
    // still present), and a subsequent successful save clears the bar, removes
    // the card, and unmounts the modal. The store clears error on markComplete
    // entry, so the stale failure message must not persist after the retry.
    const { wrapper, store } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    // First attempt fails.
    mockPatch.mockRejectedValueOnce(new Error('Server error'))
    await wrapper
      .findComponent({ name: 'EditModal' })
      .vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    const errorBar = wrapper.find('.status-bar.error')
    expect(errorBar.exists()).toBe(true)
    expect(errorBar.text()).toBe('Failed to load recommendations: Server error')
    expect(store.items.length).toBe(2)
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(true)

    // Retry succeeds: error bar clears, card removed, modal unmounts.
    mockPatch.mockResolvedValueOnce({})
    await wrapper
      .findComponent({ name: 'EditModal' })
      .vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    expect(wrapper.find('.status-bar.error').exists()).toBe(false)
    expect(store.error).toBe('')
    expect(store.items.map((i) => i.db_id)).toEqual([2])
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)
  })
})
