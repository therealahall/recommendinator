import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicatesPage from './DuplicatesPage.vue'
import type { DuplicateSuggestion } from '@/types/api'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn(),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

enableAutoUnmount(afterEach)

function page(blocks: DuplicateSuggestion[], skippedNote = '') {
  return { total: blocks.length, skipped_note: skippedNote, suggestions: blocks }
}

function block(...ids: number[]): DuplicateSuggestion {
  return {
    content_type: 'book',
    evidence: 'normalized_title',
    evidence_label: 'same title',
    evidence_detail: 'row',
    survivor_id: ids[0],
    copies: ids.map((db_id) => ({
      db_id,
      title: `Row ${db_id}`,
      source: 'calibre',
      creator: null,
      release_year: null,
      also_offered: '',
    })),
  }
}

function mountPage() {
  return mount(DuplicatesPage, { attachTo: document.body })
}

function alertOf(wrapper: ReturnType<typeof mountPage>) {
  return wrapper.get('[role="alert"]')
}

describe('DuplicatesPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockDelete.mockReset()
  })

  it('hands focus to the refusal a part-way merge drew, its block being gone', async () => {
    // Reported: the block silently shrank to two copies and said nothing. The
    // reload re-keys it, so the row that would have printed the refusal is gone,
    // and focusing the survivor instead scrolls the refusal off the screen.
    const offers = [block(10, 11, 12), block(10, 12)]
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates' ? page([offers.shift() ?? block(10, 12)]) : [],
    )
    mockPost.mockResolvedValueOnce({ survivor_title: 'Row 10', absorbed_title: 'Row 11' })
    mockPost.mockRejectedValueOnce(new Error('Item 11 is already merged into 10.'))
    const wrapper = mountPage()
    await flushPromises()

    const keepFirst = wrapper
      .findAll('button')
      .find((one) => one.text().includes('keeping “Row 10”'))!
    ;(keepFirst.element as HTMLElement).focus()
    await keepFirst.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(alertOf(wrapper).element)
    expect(alertOf(wrapper).text()).toContain('Item 11 is already merged into 10.')
    expect(wrapper.get('.dup-list').text()).not.toContain('already merged into 10.')
    expect(wrapper.get('.dup-list').text()).not.toContain('Row 11')
  })

  it('hands focus to the refusal a merge drew with its block still on the list', async () => {
    // Nothing merged, so the block reloads keyed as it was and keeps the button.
    const offer = page([block(10, 11)])
    mockGet.mockImplementation(async (url) => (url === '/duplicates' ? offer : []))
    mockPost.mockRejectedValue(new Error('Item 11 is already merged into 10.'))
    const wrapper = mountPage()
    await flushPromises()
    const keep = wrapper.findAll('button').find((one) => one.text().includes('keeping'))!
    ;(keep.element as HTMLElement).focus()
    await keep.trigger('click')
    await flushPromises()

    expect(alertOf(wrapper).text()).toContain('already merged into 10.')
    expect(document.activeElement).toBe(alertOf(wrapper).element)
  })

  it('reports a refused offer again, whose row the reload took off the list', async () => {
    // Lifted from the CLI first, the DELETE 404s and the row is already gone.
    const pair = { one_id: 20, one_title: 'Row 20', other_id: 21, other_title: 'Row 21' }
    mockGet.mockImplementation(async (url: string) => {
      if (url === '/duplicates') return page([])
      if (url === '/merges') return []
      return mockDelete.mock.calls.length === 0 ? [pair] : []
    })
    mockDelete.mockRejectedValue(new Error('Items 20 and 21 are not a declined pair.'))
    const wrapper = mountPage()
    await flushPromises()

    const offer = wrapper.findAll('button').find((one) => one.text() === 'Offer again')!
    ;(offer.element as HTMLElement).focus()
    await offer.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(alertOf(wrapper).element)
    expect(alertOf(wrapper).text()).toContain('are not a declined pair.')
  })

  it('says a work was left unsearched rather than showing its empty state', async () => {
    // Counted in neither number, a skipped work reads as a library with none.
    const note = '1 work is not offered: too many copies to review at once.'
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates' ? page([], note) : [],
    )
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain(note)
    expect(wrapper.text()).not.toContain('Nothing looks like the same work twice.')
  })

  it('keeps the alert in the tree while silent, so a refusal reads as a change', async () => {
    // Inserted with v-if once it has words, or hidden with v-show until then,
    // the refusal above never reaches a screen reader (WCAG 4.1.3).
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates' ? page([]) : [],
    )
    const wrapper = mountPage()
    await flushPromises()

    const alert = wrapper.get('[role="alert"]')

    expect(alert.text()).toBe('')
    expect(alert.isVisible()).toBe(true)
  })
})
