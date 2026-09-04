import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateHistory from './DuplicateHistory.vue'
import { useDuplicatesStore } from '@/stores/duplicates'
import type { MergeRecord } from '@/types/api'

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
    const store = useDuplicatesStore()
    store.merges = [{ ...merge(1, 10, 11), evidence_label: 'your choice (deadhouse gates)' }]

    const wrapper = mount(DuplicateHistory)

    expect(wrapper.get('.dup-log-meta').text()).toContain('your choice (deadhouse gates)')
  })

  it('leaves focus in the history when an undo unmounts the row holding it', async () => {
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

})
