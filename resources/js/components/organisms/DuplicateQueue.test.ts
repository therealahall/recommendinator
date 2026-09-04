import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateQueue from './DuplicateQueue.vue'
import { useDuplicatesStore } from '@/stores/duplicates'
import type { DuplicateSuggestion } from '@/types/api'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

enableAutoUnmount(afterEach)

const EDITIONS = ['', ' (paperback)', ' (hardback)']

function suggestion(
  title: string,
  survivorId: number,
  copies = 2,
): DuplicateSuggestion {
  return {
    content_type: 'book',
    evidence: 'normalized_title',
    evidence_label: 'same title',
    evidence_detail: title.toLowerCase(),
    survivor_id: survivorId,
    copies: EDITIONS.slice(0, copies).map((edition, index) => ({
      db_id: survivorId + index,
      title: `${title}${edition}`,
      source: 'calibre',
      creator: null,
      release_year: null,
      also_offered: '',
    })),
  }
}

function mountQueue() {
  return mount(DuplicateQueue, { attachTo: document.body })
}

function dismissButton(wrapper: ReturnType<typeof mountQueue>, title: string) {
  return wrapper
    .findAll('li')
    .find((row) => row.text().includes(title))!
    .findAll('button')
    .find((one) => one.text().startsWith(`“${title}” from`))!
}

function decidesBlocks(store: ReturnType<typeof useDuplicatesStore>): void {
  vi.spyOn(store, 'declineCopy').mockImplementation(async (copyId: number) => {
    store.suggestions = store.suggestions.filter((one) => one.survivor_id !== copyId)
  })
}

describe('DuplicateQueue', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('leaves focus on the pair that took the decided one’s place, not above it', async () => {
    const store = useDuplicatesStore()
    store.suggestions = [
      suggestion('Alpha', 10),
      suggestion('Beta', 20),
      suggestion('Gamma', 30),
    ]
    decidesBlocks(store)
    const wrapper = mountQueue()

    const beta = dismissButton(wrapper, 'Beta')
    ;(beta.element as HTMLElement).focus()
    await beta.trigger('click')
    await flushPromises()

    expect(document.activeElement).not.toBe(document.body)
    expect(document.activeElement?.closest('li')?.textContent).toContain('Gamma')
  })

  it('holds focus in the queue when the last pair leaves the list', async () => {
    const store = useDuplicatesStore()
    store.suggestions = [suggestion('Alpha', 10)]
    decidesBlocks(store)
    const wrapper = mountQueue()

    const only = dismissButton(wrapper, 'Alpha')
    ;(only.element as HTMLElement).focus()
    await only.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.element)
  })

  it('lands on the block that moved up, when the decision removed two of them', async () => {
    const store = useDuplicatesStore()
    store.suggestions = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'].map(
      (title, position) => suggestion(title, 10 * (position + 1)),
    )
    vi.spyOn(store, 'declineCopy').mockImplementation(async () => {
      store.suggestions = store.suggestions.filter(
        (one) => !['Beta', 'Gamma'].includes(one.copies[0].title),
      )
    })
    const wrapper = mountQueue()

    const gamma = dismissButton(wrapper, 'Gamma')
    ;(gamma.element as HTMLElement).focus()
    await gamma.trigger('click')
    await flushPromises()

    expect(document.activeElement?.closest('li')?.textContent).toContain('Delta')
  })

  it('stays on the block a part-way merge left behind, not the next work', async () => {
    const store = useDuplicatesStore()
    store.suggestions = [suggestion('Alpha', 10, 3), suggestion('Beta', 20)]
    vi.spyOn(store, 'merge').mockImplementation(async () => {
      store.suggestions = [suggestion('Alpha', 10), suggestion('Beta', 20)]
    })
    const wrapper = mountQueue()

    const keep = wrapper
      .findAll('button')
      .find((one) => one.text().includes('keeping “Alpha”'))!
    ;(keep.element as HTMLElement).focus()
    await keep.trigger('click')
    await flushPromises()

    expect(document.activeElement?.closest('li')?.textContent).toContain('Alpha')
    expect(document.activeElement?.closest('li')?.textContent).not.toContain('Beta')
  })

  it('leaves an operator who tabbed on mid-reload where they went', async () => {
    const store = useDuplicatesStore()
    store.suggestions = [suggestion('Alpha', 10), suggestion('Beta', 20)]
    const wrapper = mountQueue()
    const beta = dismissButton(wrapper, 'Beta')
    ;(beta.element as HTMLElement).focus()

    store.loading = true
    await nextTick()

    expect(document.activeElement).toBe(beta.element)
  })

})
