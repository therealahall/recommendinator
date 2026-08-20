import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateQueue from './DuplicateQueue.vue'
import { pairKey, useDuplicatesStore } from '@/stores/duplicates'
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
    survivor: side(survivorId, title),
    absorbed: side(survivorId + 1, `${title} (paperback)`),
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
    .find((one) => one.text() === 'Not duplicates')!
}

/** Decides the pair the way the store does: by taking it out of the offer. */
function decidesPairs(store: ReturnType<typeof useDuplicatesStore>): void {
  vi.spyOn(store, 'declinePair').mockImplementation(async (oneId: number) => {
    store.suggestions = store.suggestions.filter((one) => one.survivor.db_id !== oneId)
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
    decidesPairs(store)
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
    decidesPairs(store)
    const wrapper = mountQueue()

    const only = dismissButton(wrapper, 'Alpha')
    ;(only.element as HTMLElement).focus()
    await only.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.element)
  })

  it('prints a refusal on the pair that drew it', async () => {
    // A page of pairs runs well past the page-level alert at the top.
    const store = useDuplicatesStore()
    store.suggestions = [suggestion('Alpha', 10), suggestion('Beta', 20)]
    store.error = 'A book cannot absorb a video_game.'
    store.errorKey = `merge:${pairKey(20, 21)}`
    const wrapper = mountQueue()

    const card = (title: string) =>
      wrapper.findAll('li').find((row) => row.text().includes(title))!

    expect(card('Beta').text()).toContain('A book cannot absorb a video_game.')
    expect(card('Alpha').text()).not.toContain('A book cannot absorb a video_game.')
  })
})
