import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import RecommendationsPage from './RecommendationsPage.vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import type { ContentItemResponse } from '@/types/api'

function makeFullItem(overrides: Partial<ContentItemResponse> = {}): ContentItemResponse {
  return {
    id: 'test-1',
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
    raw: vi.fn(),
  }),
}))

vi.mock('@/composables/useSse', () => ({ readSseStream: vi.fn() }))

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
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', llm_reasoning: null, score_breakdown: {}, variety_penalty: 0 },
      { db_id: 2, title: 'B', score: 0.8, similarity_score: 0, preference_score: 0, reasoning: '', llm_reasoning: null, score_breakdown: {}, variety_penalty: 0 },
    ])
    await store.fetch(false)
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

  it('shows the second item when edit is reopened on a different card (no modal state bleed)', async () => {
    // The modal seeds its local form refs from props at setup. Because it is
    // v-if-gated on editingItem, closing must fully unmount it so reopening on
    // another card renders that card's data, not the first card's.
    const { wrapper } = await mountWithItems()
    const buttons = wrapper.findAll('.btn-complete')

    mockGet.mockResolvedValue(makeFullItem({ db_id: 1, title: 'A', content_type: 'book', status: 'unread' }))
    await buttons[0].trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'EditModal' }).props('item').title).toBe('A')

    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('close')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)

    mockGet.mockResolvedValue(makeFullItem({ db_id: 2, title: 'B', content_type: 'movie', status: 'completed', rating: 5 }))
    await buttons[1].trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'EditModal' }).props('item').title).toBe('B')
  })

  it('closes the modal when the user cancels', async () => {
    const { wrapper } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(true)

    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('close')
    await flushPromises()

    expect(wrapper.findComponent({ name: 'EditModal' }).exists()).toBe(false)
  })

  it('restores focus to the triggering control when the modal is cancelled', async () => {
    // WCAG 2.4.3: cancelling the modal must return focus to the card button that
    // opened it (still in the DOM), not strand the keyboard user at <body>.
    const { wrapper } = await mountWithItems()
    const trigger = wrapper.find('.btn-complete').element as HTMLElement
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('close')
    await flushPromises()

    expect(document.activeElement).toBe(trigger)
  })

  it('moves focus to the heading when a save removes the only remaining card', async () => {
    // Edge the next-card tests do not cover: completing the LAST card removes the
    // rec list entirely (v-if), so recList.value is null and there is no next
    // .btn-complete to focus. Focus must fall back to the page heading
    // (tabindex="-1") so the keyboard user is not stranded at <body>.
    wrapper = mount(RecommendationsPage, { global: { stubs }, attachTo: document.body })
    const store = useRecommendationsStore()
    mockGet.mockResolvedValue([
      { db_id: 1, title: 'A', score: 0.9, similarity_score: 0, preference_score: 0, reasoning: '', llm_reasoning: null, score_breakdown: {}, variety_penalty: 0 },
    ])
    await store.fetch(false)
    await flushPromises()

    const onlyTrigger = wrapper.find('.btn-complete').element as HTMLElement
    onlyTrigger.focus()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    mockPatch.mockResolvedValue({})
    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    expect(store.items.length).toBe(0)
    expect(wrapper.find('.btn-complete').exists()).toBe(false)
    const heading = wrapper.find('h2').element as HTMLElement
    expect(document.activeElement).toBe(heading)
    expect(document.activeElement).not.toBe(document.body)
  })

  it('moves focus to the next card when a save removes the triggering card', async () => {
    // The triggering card is detached after a successful save, so .focus() on it
    // would land at <body>. Focus must instead move to a remaining card action.
    const { wrapper } = await mountWithItems()
    const firstTrigger = wrapper.find('.btn-complete').element as HTMLElement
    firstTrigger.focus()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    mockPatch.mockResolvedValue({})
    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    const remaining = wrapper.find('.btn-complete').element as HTMLElement
    expect(document.contains(firstTrigger)).toBe(false)
    expect(document.activeElement).toBe(remaining)
  })

  it('keeps the modal mounted and focused when a save fails', async () => {
    // Mirror of the save-removal focus test: a rejected PATCH must not remove the
    // card or steal focus from the open form. The modal stays mounted so the user
    // can retry, and focus stays inside it rather than collapsing to <body>.
    const { wrapper, store } = await mountWithItems()

    mockGet.mockResolvedValue(makeFullItem())
    await wrapper.find('.btn-complete').trigger('click')
    await flushPromises()

    mockPatch.mockRejectedValue(new Error('Server error'))
    await wrapper.findComponent({ name: 'EditModal' }).vm.$emit('save', 1, { status: 'completed', rating: null, review: null })
    await flushPromises()

    expect(store.items.length).toBe(2)
    const modal = wrapper.findComponent({ name: 'EditModal' })
    expect(modal.exists()).toBe(true)
    // Focus must stay within the still-mounted modal, not collapse to <body> or
    // jump back out to a card action behind the dialog. Guard against the
    // body-contains-everything escape hatch first, then assert containment.
    expect(document.activeElement).not.toBe(document.body)
    expect((modal.element as HTMLElement).contains(document.activeElement)).toBe(true)
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
