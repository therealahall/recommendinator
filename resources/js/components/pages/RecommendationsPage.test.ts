import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import RecommendationsPage from './RecommendationsPage.vue'
import LoadingRows from '@/components/molecules/LoadingRows.vue'
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
    scorer_weights: {},
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

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'recommendations', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
    ],
  })
}

describe('RecommendationsPage', () => {
  let wrapper: VueWrapper | null = null
  let router: Router

  beforeEach(() => {
    setActivePinia(createPinia())
    router = testRouter()
    mockGet.mockReset()
    mockPatch.mockReset()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  async function mountWithItems() {
    wrapper = mount(RecommendationsPage, {
      global: { stubs, plugins: [router] },
      attachTo: document.body,
    })
    const store = useRecommendationsStore()
    mockGet.mockResolvedValue([
      makeRec({ db_id: 1, title: 'A', score: 0.9 }),
      makeRec({ db_id: 2, title: 'B', score: 0.8 }),
    ])
    await store.fetch()
    await flushPromises()
    return { wrapper, store }
  }

  it('draws placeholder rows while a run is in flight, and says so out loud', async () => {
    wrapper = mount(RecommendationsPage, { global: { stubs, plugins: [router] } })
    useRecommendationsStore().loading = true
    await wrapper.vm.$nextTick()

    const placeholders = wrapper.findComponent(LoadingRows)
    expect(placeholders.exists()).toBe(true)
    expect(placeholders.attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('[role="status"]').text()).toContain('Recommending')
  })

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
    // The load banner renders behind the overlay and calls every failure a
    // failure to load, so a refused save was invisible and misdescribed.
    const { wrapper, store } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

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

      expect(wrapper.get('[data-testid="recs-announce"]').text()).toContain('Ignored “A”')
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

  // Firefox maps no `value` onto posinset, so it announced "1 of 1" on #2.
  it('is a ranked list whose items carry the run rank, not their page position', async () => {
    const { wrapper, store } = await mountWithItems()
    expect(wrapper.findAll('ol > li').map((li) => li.attributes('value'))).toEqual(['1', '2'])

    store.items[0].content_type = 'movie'
    store.contentType = 'book'
    await flushPromises()

    const rows = wrapper.findAll('ol > li')
    expect(rows.map((li) => li.attributes('value'))).toEqual(['2'])
    expect(rows.map((li) => li.attributes('aria-posinset'))).toEqual(['2'])
    expect(rows.map((li) => li.attributes('aria-setsize'))).toEqual(['2'])
  })

  it('mounts the run line silent and keeps it mounted through an emptying filter', async () => {
    wrapper = mount(RecommendationsPage, {
      global: { stubs, plugins: [router] },
      attachTo: document.body,
    })
    expect(wrapper.get('.run-line').text()).toBe('')

    const store = useRecommendationsStore()
    mockGet.mockResolvedValue([makeRec({ db_id: 1, content_type: 'book' })])
    await store.fetch()
    await flushPromises()
    store.contentType = 'movie'
    await flushPromises()

    const line = wrapper.get('.run-line')
    expect(line.attributes('role')).toBe('status')
    expect(line.text()).toContain('0 of 1')
  })

  // The selector filters what came back; only the Recommend button runs anything.
  it('narrows the list to one type without asking for another run', async () => {
    wrapper = mount(RecommendationsPage, {
      global: { stubs, plugins: [router] },
      attachTo: document.body,
    })
    const store = useRecommendationsStore()
    mockGet.mockResolvedValue([
      makeRec({ db_id: 1, title: 'Hyperion', content_type: 'book' }),
      makeRec({ db_id: 2, title: 'Arrival', content_type: 'movie', score: 0.8 }),
    ])
    await store.fetch()
    await flushPromises()

    store.contentType = 'movie'
    await flushPromises()

    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Arrival')
    expect(wrapper.text()).not.toContain('Hyperion')
    // Nothing takes focus on a filter, so the count is how a screen reader
    // learns the list moved (WCAG 4.1.3). It must survive one emptying it.
    expect(wrapper.get('.run-line').attributes('role')).toBe('status')
    expect(wrapper.get('.run-line').text()).toContain('1 of 2')
  })

  describe('the empty state', () => {
    // Two causes read identically otherwise: no run yet, and a run with nothing
    // of this type left.
    async function emptyAfterRun(type: string) {
      wrapper = mount(RecommendationsPage, {
        global: { stubs, plugins: [router] },
        attachTo: document.body,
      })
      const store = useRecommendationsStore()
      store.contentType = type
      mockGet.mockResolvedValue([])
      await store.fetch()
      await flushPromises()
      return wrapper
    }

    it('offers a first run, and hands the keyboard the heading rather than <body>', async () => {
      wrapper = mount(RecommendationsPage, {
        global: { stubs, plugins: [router] },
        attachTo: document.body,
      })
      mockGet.mockResolvedValue([makeRec({ db_id: 1 })])
      const run = wrapper.get('[data-testid="recs-empty-run"]')
      ;(run.element as HTMLElement).focus()

      await run.trigger('click')
      await flushPromises()

      expect(mockGet).toHaveBeenCalled()
      expect(document.activeElement).toBe(wrapper.get('h2').element)
    })

    it('names the type nothing was left of, once a run returned nothing', async () => {
      const page = await emptyAfterRun('tv_show')

      expect(page.get('[data-testid="recs-empty"]').text()).toContain('tv show')
    })

    it('names no type when the run that came back empty covered all four', async () => {
      const page = await emptyAfterRun('')

      const empty = page.get('[data-testid="recs-empty"]').text()
      expect(empty).toContain('Nothing left')
      expect(empty).not.toContain('tv show')
    })

    it('offers the rest of the run back, and keeps the keyboard off <body>', async () => {
      wrapper = mount(RecommendationsPage, {
        global: { stubs, plugins: [router] },
        attachTo: document.body,
      })
      const store = useRecommendationsStore()
      mockGet.mockResolvedValue([makeRec({ db_id: 1, content_type: 'book' })])
      await store.fetch()
      await flushPromises()

      store.contentType = 'movie'
      await flushPromises()
      expect(wrapper.get('[data-testid="recs-empty"]').text()).toContain('No movie in this run')

      const showAll = wrapper.get('[data-testid="recs-show-all"]')
      ;(showAll.element as HTMLElement).focus()
      await showAll.trigger('click')
      await flushPromises()

      expect(store.contentType).toBe('')
      expect(mockGet).toHaveBeenCalledTimes(1)
      expect(wrapper.find('[data-testid="recs-empty"]').exists()).toBe(false)
      expect(document.activeElement).toBe(wrapper.get('h2').element)
    })

    it('says a run has happened differently from one that has not', async () => {
      const before = mount(RecommendationsPage, {
        global: { stubs, plugins: [router] },
        attachTo: document.body,
      })
      const said = before.get('[data-testid="recs-empty"]').text()
      before.unmount()

      const after = await emptyAfterRun('book')

      expect(after.get('[data-testid="recs-empty"]').text()).not.toBe(said)
    })
  })
})
