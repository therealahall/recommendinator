import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateQueue from './DuplicateQueue.vue'
import { decisionKey, useDuplicatesStore } from '@/stores/duplicates'
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

function suggestion(title: string, survivorId: number): DuplicateSuggestion {
  const side = (db_id: number, name: string) => ({
    db_id,
    title: name,
    source: 'calibre',
    creator: null,
    release_year: null,
  })
  return {
    content_type: 'book',
    evidence: 'normalized_title',
    evidence_label: 'same title',
    evidence_detail: title.toLowerCase(),
    survivor_id: survivorId,
    copies: [side(survivorId, title), side(survivorId + 1, `${title} (paperback)`)],
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
    .find((one) => one.text() === `“${title}” is not the same work`)!
}

function decidesBlocks(store: ReturnType<typeof useDuplicatesStore>): void {
  vi.spyOn(store, 'declineCopy').mockImplementation(async (copyId: number) => {
    store.suggestions = store.suggestions.filter((one) => one.survivor_id !== copyId)
  })
}

describe('DuplicateQueue', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('leaves focus on the pair that took the decided one’s place, not above it', async () => {
    // Every decision removes its own card, so skipping is the only way past a
    // pair — focus going back to the top re-walks everything already skipped.
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
    // A veto splits one work into overlapping blocks, so a decision on the copy
    // they share drops both at once and the decided one's index overshoots.
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

  it('prints a refusal on the block that drew it', async () => {
    // A page of blocks runs well past the page-level alert at the top.
    const store = useDuplicatesStore()
    store.suggestions = [suggestion('Alpha', 10), suggestion('Beta', 20)]
    store.error = 'A book cannot absorb a video_game.'
    store.errorKey = `merge:${decisionKey([20, 21])}`
    const wrapper = mountQueue()

    const card = (title: string) =>
      wrapper.findAll('li').find((row) => row.text().includes(title))!

    expect(card('Beta').text()).toContain('A book cannot absorb a video_game.')
    expect(card('Alpha').text()).not.toContain('A book cannot absorb a video_game.')
  })
})
