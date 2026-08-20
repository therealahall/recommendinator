import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import DuplicatePair from './DuplicatePair.vue'
import type { DuplicateSuggestion } from '@/types/api'

function makeSuggestion(overrides: Partial<DuplicateSuggestion> = {}): DuplicateSuggestion {
  return {
    content_type: 'book',
    evidence: 'normalized_title',
    evidence_label: 'same title',
    evidence_detail: 'deadhouse gates',
    survivor: {
      db_id: 3,
      title: 'Deadhouse Gates',
      source: 'calibre',
      creator: null,
      release_year: null,
    },
    absorbed: {
      db_id: 4,
      title: 'Deadhouse Gates (Malazan Book 2)',
      source: 'goodreads_csv',
      creator: 'Steven Erikson',
      release_year: 2000,
    },
    ...overrides,
  }
}

function mountPair(props: Partial<DuplicateSuggestion> = {}, busy = {}) {
  return mount(DuplicatePair, {
    props: { suggestion: makeSuggestion(props), merging: false, declining: false, ...busy },
  })
}

function keepButton(wrapper: ReturnType<typeof mountPair>, title: string) {
  return wrapper.findAll('button').find((one) => one.text().includes(`keeping “${title}”`))!
}

function dismissButton(wrapper: ReturnType<typeof mountPair>) {
  return wrapper.findAll('button').find((one) => one.text() === 'Not duplicates')!
}

describe('DuplicatePair', () => {
  it('merges towards whichever row the operator chose to keep', async () => {
    // The roles the API offers are a proposal, and a card that always emitted
    // them in the offered order would fold away the row that was picked.
    const wrapper = mountPair()

    await keepButton(wrapper, 'Deadhouse Gates (Malazan Book 2)').trigger('click')

    expect(wrapper.emitted('merge')![0]).toEqual([4, 3])
  })

  it('merges towards the proposed survivor when that is the one chosen', async () => {
    const wrapper = mountPair()

    await keepButton(wrapper, 'Deadhouse Gates').trigger('click')

    expect(wrapper.emitted('merge')![0]).toEqual([3, 4])
  })

  it('emits nothing more once a decision on the pair is in flight', async () => {
    // The second click would name a row the first merge is about to hide.
    const wrapper = mountPair({}, { merging: true })

    await keepButton(wrapper, 'Deadhouse Gates').trigger('click')
    await dismissButton(wrapper).trigger('click')

    expect(wrapper.emitted('merge')).toBeUndefined()
    expect(wrapper.emitted('decline')).toBeUndefined()
  })

  it('names both rows of the pair when it is dismissed', async () => {
    const wrapper = mountPair()

    await dismissButton(wrapper).trigger('click')

    expect(wrapper.emitted('decline')![0]).toEqual([3, 4])
  })

  it('warns on the looser key and says nothing extra on the save door’s own', () => {
    // Only the looser key drops a trailing parenthetical, so it is the one
    // that can pair two genuinely different editions.
    const loose = mountPair({
      evidence: 'title_qualifier',
      evidence_label: 'same title apart from a qualifier',
    })
    const exact = mountPair()

    expect(loose.find('.dup-pair-caution').exists()).toBe(true)
    expect(loose.text()).toContain('same title apart from a qualifier')
    expect(exact.find('.dup-pair-caution').exists()).toBe(false)
  })

  it('offers no way to delete either row of the pair', () => {
    // Deleting a row that a merge has hidden orphans its children with no
    // undo, and these ids are exactly the ones a merge hides.
    const wrapper = mountPair()

    expect(wrapper.text().toLowerCase()).not.toContain('delete')
  })

  it('shows each row’s provenance, so a pair can be judged without opening it', () => {
    const wrapper = mountPair()

    expect(wrapper.text()).toContain('Steven Erikson')
    expect(wrapper.text()).toContain('goodreads_csv')
    expect(wrapper.text()).toContain('calibre')
  })
})
