import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicatesPage from './DuplicatesPage.vue'
import type { DuplicateSuggestion } from '@/types/api'

const mockGet = vi.fn()
const mockPost = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

enableAutoUnmount(afterEach)

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

/** What a sighted operator reads, not what only the screen reader hears. */
function refusalOnScreen(wrapper: ReturnType<typeof mount>): string {
  const alert = wrapper.get('[role="alert"]')
  return alert.classes('sr-only') ? '' : alert.text()
}

describe('DuplicatesPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
  })

  it('shows the refusal a part-way merge drew, though the block it named is gone', async () => {
    // Reported: the block silently shrank to two copies and said nothing. The
    // reload re-keys it, so the row that would have printed the refusal is gone.
    const offers = [block(10, 11, 12), block(10, 12)]
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates'
        ? { total: 1, suggestions: [offers.shift() ?? block(10, 12)] }
        : [],
    )
    mockPost.mockResolvedValueOnce({ survivor_title: 'Row 10', absorbed_title: 'Row 11' })
    mockPost.mockRejectedValueOnce(new Error('Item 11 is already merged into 10.'))
    const wrapper = mount(DuplicatesPage)
    await flushPromises()

    const keepFirst = wrapper
      .findAll('button')
      .find((one) => one.text().includes('keeping “Row 10”'))!
    await keepFirst.trigger('click')
    await flushPromises()

    expect(refusalOnScreen(wrapper)).toContain('Item 11 is already merged into 10.')
    expect(wrapper.get('.dup-list').text()).not.toContain('already merged into 10.')
    expect(wrapper.get('.dup-list').text()).not.toContain('Row 11')
  })

  it('keeps the alert in the tree while silent, so a refusal reads as a change', async () => {
    // Inserted with v-if once it has words, or hidden with v-show until then,
    // the refusal above never reaches a screen reader (WCAG 4.1.3).
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates' ? { total: 0, suggestions: [] } : [],
    )
    const wrapper = mount(DuplicatesPage)
    await flushPromises()

    const alert = wrapper.get('[role="alert"]')

    expect(alert.text()).toBe('')
    expect(alert.isVisible()).toBe(true)
  })
})
