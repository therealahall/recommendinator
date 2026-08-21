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
    survivor_id: 3,
    copies: [
      {
        db_id: 3,
        title: 'Deadhouse Gates',
        source: 'calibre',
        creator: null,
        release_year: null,
      },
      {
        db_id: 4,
        title: 'Deadhouse Gates (Malazan Book 2)',
        source: 'goodreads_csv',
        creator: 'Steven Erikson',
        release_year: 2000,
      },
    ],
    ...overrides,
  }
}

function threeCopies(): Partial<DuplicateSuggestion> {
  const base = makeSuggestion()
  return {
    copies: [
      ...base.copies,
      {
        db_id: 5,
        title: 'Deadhouse Gates (Malazan, Book Two)',
        source: 'storygraph_csv',
        creator: null,
        release_year: null,
      },
    ],
  }
}

function mountPair(props: Partial<DuplicateSuggestion> = {}, busy = {}) {
  return mount(DuplicatePair, {
    props: {
      suggestion: makeSuggestion(props),
      merging: false,
      declining: false,
      error: '',
      ...busy,
    },
  })
}

function keepButton(wrapper: ReturnType<typeof mountPair>, title: string) {
  return wrapper.findAll('button').find((one) => one.text().includes(`keeping “${title}”`))!
}

function dismissButton(wrapper: ReturnType<typeof mountPair>, title: string) {
  return wrapper
    .findAll('button')
    .find((one) => one.text() === `“${title}” is not the same work`)!
}

describe('DuplicatePair', () => {
  it('merges every other copy into whichever one the operator chose to keep', async () => {
    // The survivor the API offers is a proposal, and a card that always emitted
    // it would fold away the copy that was picked.
    const wrapper = mountPair(threeCopies())

    await keepButton(wrapper, 'Deadhouse Gates (Malazan Book 2)').trigger('click')

    expect(wrapper.emitted('merge')![0]).toEqual([4, [3, 5]])
  })

  it('merges towards the proposed survivor when that is the one chosen', async () => {
    const wrapper = mountPair()

    await keepButton(wrapper, 'Deadhouse Gates').trigger('click')

    expect(wrapper.emitted('merge')![0]).toEqual([3, [4]])
  })

  it('emits nothing more once a decision on the block is in flight', async () => {
    // The second click would name a row the first merge is about to hide.
    const wrapper = mountPair({}, { merging: true })

    await keepButton(wrapper, 'Deadhouse Gates').trigger('click')
    await dismissButton(wrapper, 'Deadhouse Gates').trigger('click')

    expect(wrapper.emitted('merge')).toBeUndefined()
    expect(wrapper.emitted('decline')).toBeUndefined()
  })

  it('sets a dismissed copy apart from every other copy in the block', async () => {
    // Refusing it against only one of them leaves the block still offering it
    // against the rest, which is the same copy twice over again.
    const wrapper = mountPair(threeCopies())

    await dismissButton(wrapper, 'Deadhouse Gates (Malazan, Book Two)').trigger('click')

    expect(wrapper.emitted('decline')![0]).toEqual([5, [3, 4]])
  })

  it('lists every copy of the work, marking only the one it proposes keeping', () => {
    // Without the mark every copy reads the same, and the one-click default the
    // API proposes is invisible.
    const wrapper = mountPair(threeCopies())

    const marked = wrapper
      .findAll('.dup-side')
      .filter((side) => side.text().includes('suggested to keep'))

    expect(wrapper.findAll('.dup-side')).toHaveLength(3)
    expect(marked.map((side) => side.text().includes('Deadhouse Gates'))).toEqual([true])
    expect(wrapper.text()).toContain('Deadhouse Gates (Malazan, Book Two)')
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

  it('shows each row’s provenance, so a pair can be judged without opening it', () => {
    const wrapper = mountPair()

    expect(wrapper.text()).toContain('Steven Erikson')
    expect(wrapper.text()).toContain('goodreads_csv')
    expect(wrapper.text()).toContain('calibre')
  })
})
