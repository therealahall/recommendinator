import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import RecommendationsPage from './RecommendationsPage.vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import type { ContentItemResponse, RecommendationResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 1,
    title: 'A',
    author: null,
    content_type: 'book',
    cover_url: null,
    series: null,
    series_index: null,
    score: 0.9,
    reasoning: '',
    score_breakdown: {},
    variety_penalty: 0,
    contributing_items: [],
    adaptations: [],
    ...overrides,
  }
}

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
    release_year: null,
    series: null,
    series_index: null,
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
      makeRec({ db_id: 1, title: 'A', score: 0.9 }),
      makeRec({ db_id: 2, title: 'B', score: 0.8 }),
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

  it('opens Mark complete on completed, not on the item\'s unread status', async () => {
    // The card vanished on save either way, so a rating saved from the
    // preselected "unread" left an unread item that looked marked complete.
    const { wrapper } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    expect((wrapper.get('#edit-status').element as HTMLSelectElement).value).toBe('completed')
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

  it('says a refused save in the dialog, never on the page, and clears it on retry', async () => {
    // End-to-end through the page: the load banner renders behind the overlay
    // and calls every failure a failure to load, so a refused save was invisible
    // and misdescribed. The card and the modal stay until a save is accepted.
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

    const dialog = wrapper.find('[aria-modal="true"]')
    expect(dialog.find('[role="alert"]').text()).toBe('Server error')
    expect(wrapper.find('.status-bar.error').exists()).toBe(false)
    expect(store.items.length).toBe(2)

    // Retry succeeds: card removed, modal unmounts, refusal gone with it.
    mockPatch.mockResolvedValueOnce({})
    await wrapper
      .findComponent({ name: 'EditModal' })
      .vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    expect(store.editError).toBe('')
    expect(store.items.map((i) => i.db_id)).toEqual([2])
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)
  })

  describe('ignoring a recommendation', () => {
    it('leaves an undo where the card was, and puts the keyboard on it', async () => {
      const { wrapper } = await mountWithItems()
      mockPatch.mockResolvedValue({})

      await wrapper.find('[data-testid="ignore-btn-1"]').trigger('click')
      await flushPromises()

      const undo = wrapper.get('[data-testid="undo-ignore-1"]')
      expect(wrapper.get('[data-testid="ignored-row-1"]').text()).toContain('A')
      expect(document.activeElement).toBe(undo.element)
    })

    it('announces the removal, which nothing else on screen says', async () => {
      const { wrapper } = await mountWithItems()
      mockPatch.mockResolvedValue({})

      await wrapper.find('[data-testid="ignore-btn-1"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('[role="status"]').text()).toContain('Ignored “A”')
    })

    it('brings the card back on Undo, with focus on the Ignore that returned', async () => {
      const { wrapper } = await mountWithItems()
      mockPatch.mockResolvedValue({})

      await wrapper.find('[data-testid="ignore-btn-1"]').trigger('click')
      await flushPromises()
      await wrapper.find('[data-testid="undo-ignore-1"]').trigger('click')
      await flushPromises()

      const ignore = wrapper.get('[data-testid="ignore-btn-1"]')
      expect(wrapper.find('[data-testid="ignored-row-1"]').exists()).toBe(false)
      expect(document.activeElement).toBe(ignore.element)
    })

    it('says a refused ignore instead of leaving the card silently in place', async () => {
      const { wrapper } = await mountWithItems()
      mockPatch.mockRejectedValue(new Error('Server error'))

      await wrapper.find('[data-testid="ignore-btn-1"]').trigger('click')
      await flushPromises()

      const alert = wrapper.get('#rec-ignore-error')
      expect(alert.text()).toContain('Server error')
      expect(wrapper.find('[data-testid="ignored-row-1"]').exists()).toBe(false)
      expect(document.activeElement).toBe(alert.element)
    })
  })

  describe('the empty state', () => {
    it('invites a first Generate before one has run', () => {
      wrapper = mount(RecommendationsPage, { global: { stubs }, attachTo: document.body })

      expect(wrapper.get('[data-testid="recs-empty"]').text()).toContain('Click Generate')
    })

    it('names the type and the next step once a Generate returned nothing', async () => {
      wrapper = mount(RecommendationsPage, { global: { stubs }, attachTo: document.body })
      const store = useRecommendationsStore()
      store.contentType = 'tv_show'
      mockGet.mockResolvedValue([])
      await store.fetch()
      await flushPromises()

      const empty = wrapper.get('[data-testid="recs-empty"]').text()
      expect(empty).not.toContain('Click Generate')
      expect(empty).toContain('No tv show recommendations')
      expect(empty).toContain('syncing a source')
    })
  })
})
