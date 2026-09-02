import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecCard from './RecCard.vue'
import type { RecommendationResponse, RelatedItemResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 7,
    title: 'Test',
    author: 'Author',
    content_type: 'book',
    cover_url: null,
    series: null,
    series_index: null,
    score: 0.5,
    reasoning: 'Because',
    score_breakdown: {},
    variety_penalty: 0,
    contributing_items: [],
    adaptations: [],
    ...overrides,
  }
}

function related(overrides: Partial<RelatedItemResponse> = {}): RelatedItemResponse {
  return {
    db_id: 11,
    title: 'A Memory Called Empire',
    author: 'Arkady Martine',
    content_type: 'book',
    cover_url: null,
    ...overrides,
  }
}

describe('RecCard', () => {
  it('emits ignore with the db_id when the ignore button is clicked', async () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec(), rank: 1 },
    })
    await wrapper.find('.btn-ignore').trigger('click')
    expect(wrapper.emitted('ignore')![0]).toEqual([7])
  })

  it('emits complete with the db_id when the complete button is clicked', async () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec(), rank: 1 },
    })
    await wrapper.find('.btn-complete').trigger('click')
    expect(wrapper.emitted('complete')).toHaveLength(1)
    expect(wrapper.emitted('complete')![0]).toEqual([7])
  })

  it('gives the action buttons accessible names whose visible text leads (WCAG 2.5.3)', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ title: 'Dune' }), rank: 1 },
    })
    expect(wrapper.find('.btn-complete').attributes('aria-label')).toBe('Mark complete: Dune')
    expect(wrapper.find('.btn-ignore').attributes('aria-label')).toBe('Ignore: Dune')
  })

  it('names the series the recommended title no longer carries', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ series: 'The Expanse', series_index: 2 }), rank: 1 },
    })
    expect(wrapper.find('.rec-series').text()).toContain('The Expanse #2')
  })

  it('omits the action buttons when there is no db_id', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ db_id: null }), rank: 1 },
    })
    expect(wrapper.find('.btn-complete').exists()).toBe(false)
    expect(wrapper.find('.btn-ignore').exists()).toBe(false)
  })

  it('renders the score as a percentage, never as a decimal out of one', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ score: 0.88 }), rank: 1 },
    })

    expect(wrapper.get('.rec-score').text()).toContain('88%')
    expect(wrapper.text()).not.toContain('0.88')
  })

  it('draws the missing-art state a null cover_url means, not a broken image', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ cover_url: null, content_type: 'movie' }), rank: 1 },
    })

    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('No cover art for Test')
  })

  it('names every cited library item and says in words how it counted', () => {
    const wrapper = mount(RecCard, {
      props: {
        rec: makeRec({
          content_type: 'movie',
          contributing_items: [
            related({ db_id: 11, title: 'A Memory Called Empire' }),
            related({ db_id: 12, title: 'Ancillary Justice' }),
          ],
        }),
        rank: 1,
      },
    })

    const pills = wrapper.findAll('.rec-evidence .badge')
    expect(pills).toHaveLength(2)
    expect(pills[0].text()).toContain('A Memory Called Empire')
    expect(pills[0].text()).toContain('contributed directly')
    expect(pills[1].text()).toContain('Ancillary Justice')
    expect(pills[1].text()).toContain('contributed directly')
  })

  it('says an inferred citation is inferred in words, not by its edge alone', () => {
    const wrapper = mount(RecCard, {
      props: {
        rec: makeRec({ adaptations: [related({ title: 'Dune, the novel' })] }),
        rank: 1,
      },
    })

    const pill = wrapper.get('.rec-evidence .badge')
    expect(pill.text()).toContain('inferred')
  })

  // The engine hands the same liked book back in both lists — proven in
  // tests/test_reference_index.py — and its own reasoning sentence dedupes them.
  it('cites a library item once when it is both the adaptation and a direct signal', () => {
    const dune = related({ db_id: 11, title: 'Dune' })
    const wrapper = mount(RecCard, {
      props: {
        rec: makeRec({ content_type: 'movie', contributing_items: [dune], adaptations: [dune] }),
        rank: 1,
      },
    })

    expect(wrapper.findAll('.rec-evidence .badge')).toHaveLength(1)
  })

  it('still shows the inferred citation when direct ones would fill the row', () => {
    const wrapper = mount(RecCard, {
      props: {
        rec: makeRec({
          content_type: 'movie',
          contributing_items: [
            related({ db_id: 1, title: 'Ancillary Justice' }),
            related({ db_id: 2, title: 'Hyperion' }),
            related({ db_id: 3, title: 'Neuromancer' }),
          ],
          adaptations: [related({ db_id: 4, title: 'Dune' })],
        }),
        rank: 1,
      },
    })

    expect(wrapper.get('.rec-evidence').text()).toContain('Dune')
  })

  it('opens the breakdown from the collapsed score, which is the only control for it', async () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ score_breakdown: { genre_match: 0.8 } }), rank: 1 },
    })
    const score = wrapper.get('.rec-score')
    const details = wrapper.get('.score-details')

    expect(score.attributes('aria-expanded')).toBe('false')
    expect(score.attributes('aria-controls')).toBe(details.attributes('id'))
    expect(details.attributes('hidden')).toBeDefined()

    await score.trigger('click')

    expect(wrapper.get('.rec-score').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.score-details').attributes('hidden')).toBeUndefined()
  })

  it('leads the score control\'s accessible name with its visible words (WCAG 2.5.3)', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ score: 0.78, score_breakdown: { genre_match: 0.8 } }), rank: 1 },
    })

    expect(wrapper.get('.rec-score').text()).toContain('Why this')
    expect(wrapper.get('.rec-score .sr-only').text()).toMatch(/^Why this/)
  })

  it('offers no disclosure when there is no breakdown behind it', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ score_breakdown: {}, variety_penalty: 0 }), rank: 1 },
    })

    expect(wrapper.get('.rec-score').element.tagName).toBe('SPAN')
    expect(wrapper.find('.score-details').exists()).toBe(false)
  })
})
