import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateHistory from './DuplicateHistory.vue'
import { useDuplicatesStore } from '@/stores/duplicates'
import type { DeclinedPair, MergeRecord } from '@/types/api'

const mockGet = vi.fn()
const mockDelete = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

enableAutoUnmount(afterEach)

function merge(id: number, survivorId: number, absorbedId: number): MergeRecord {
  return {
    id,
    survivor_id: survivorId,
    survivor_title: `Row ${survivorId}`,
    absorbed_id: absorbedId,
    absorbed_title: `Row ${absorbedId}`,
    evidence: 'manual',
    evidence_label: 'your choice',
    evidence_detail: null,
    merged_at: '2026-08-20 09:30:00',
  }
}

function declined(oneId: number, otherId: number): DeclinedPair {
  return {
    one_id: oneId,
    one_title: `Row ${oneId}`,
    other_id: otherId,
    other_title: `Row ${otherId}`,
  }
}

function mountHistory() {
  return mount(DuplicateHistory, { attachTo: document.body })
}

function row(wrapper: ReturnType<typeof mountHistory>, title: string) {
  return wrapper.findAll('.dup-log-row').find((one) => one.text().includes(title))!
}

describe('DuplicateHistory', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockDelete.mockReset()
  })

  it('says why a blocked undo is blocked, and does not run it when pressed anyway', () => {
    // aria-disabled keeps the control focusable on purpose, so the reason has
    // to reach a screen reader and the handler has to be what refuses.
    const store = useDuplicatesStore()
    store.merges = [merge(1, 10, 11), merge(2, 10, 12)]
    const undo = vi.spyOn(store, 'undoMerge')

    const wrapper = mount(DuplicateHistory)
    const buttons = wrapper.findAll('.dup-log-row button')
    const blocked = buttons[1]
    blocked.trigger('click')

    expect(blocked.attributes('aria-disabled')).toBe('true')
    expect(wrapper.get(`#${blocked.attributes('aria-describedby')}`).text()).toContain(
      'Undo merge 2 first',
    )
    expect(buttons[0].attributes('aria-disabled')).toBeUndefined()
    expect(undo).not.toHaveBeenCalled()
  })

  it('renders the evidence wording the payload carries rather than one of its own', () => {
    // Both surfaces print the server's label, so a map here would drift from
    // the CLI's the first time the wording changed.
    const store = useDuplicatesStore()
    store.merges = [{ ...merge(1, 10, 11), evidence_label: 'your choice (deadhouse gates)' }]

    const wrapper = mount(DuplicateHistory)

    expect(wrapper.get('.dup-log-meta').text()).toContain('your choice (deadhouse gates)')
  })

  it('leaves focus in the history when an undo unmounts the row holding it', async () => {
    // Dropped to <body>, the next Tab restarts at the top of the document,
    // behind the sidebar and the whole queue (WCAG 2.4.3).
    const store = useDuplicatesStore()
    store.merges = [merge(1, 10, 11), merge(2, 20, 21)]
    vi.spyOn(store, 'undoMerge').mockImplementation(async (id: number) => {
      store.merges = store.merges.filter((one) => one.id !== id)
    })
    const wrapper = mountHistory()

    const undo = row(wrapper, 'Row 11').get('button')
    ;(undo.element as HTMLElement).focus()
    await undo.trigger('click')
    await flushPromises()

    expect(document.activeElement).not.toBe(document.body)
    expect(wrapper.element.contains(document.activeElement)).toBe(true)
  })

  it('prints a refused offer again beside the control, and names it as its reason', async () => {
    // Offering again has no precomputed block, so the click is expected to
    // fail sometimes, and the only other sign is the label reverting.
    const store = useDuplicatesStore()
    store.declined = [declined(10, 11), declined(20, 21)]
    mockDelete.mockRejectedValue(new Error('Undo merge 7 first — it absorbed “Row 20”.'))
    // A refusal reloads before it reports, and one that failed is still in force.
    mockGet.mockImplementation(async (url: string) =>
      url === '/duplicates'
        ? { total: 0, suggestions: [] }
        : [declined(10, 11), declined(20, 21)],
    )
    const wrapper = mountHistory()

    await row(wrapper, 'Row 21').get('button').trigger('click')
    await flushPromises()

    const refused = row(wrapper, 'Row 21')
    expect(refused.text()).toContain('Undo merge 7 first')
    expect(refused.get('button').attributes('aria-describedby')).toBe(
      refused.get('.dup-log-reason').attributes('id'),
    )
    expect(row(wrapper, 'Row 11').find('.dup-log-reason').exists()).toBe(false)
  })
})
