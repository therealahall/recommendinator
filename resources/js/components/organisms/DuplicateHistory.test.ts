import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import DuplicateHistory from './DuplicateHistory.vue'
import { useDuplicatesStore } from '@/stores/duplicates'
import type { MergeRecord } from '@/types/api'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }),
}))

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

describe('DuplicateHistory', () => {
  beforeEach(() => setActivePinia(createPinia()))

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
})
