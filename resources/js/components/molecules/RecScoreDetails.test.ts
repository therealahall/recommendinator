import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecScoreDetails from './RecScoreDetails.vue'
import type { RecommendationResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 1,
    title: 'Test',
    author: 'Author',
    content_type: 'book',
    cover_url: null,
    series: null,
    series_index: null,
    score: 0.5,
    reasoning: 'Because',
    score_breakdown: { genre_match: 0.8 },
    scorer_weights: {},
    variety_penalty: 0,
    contributing_items: [],
    adaptations: [],
    ...overrides,
  }
}

describe('RecScoreDetails', () => {
  it('states the variety penalty as the share of the match it took off', () => {
    const wrapper = mount(RecScoreDetails, {
      props: { rec: makeRec({ variety_penalty: 0.25 }), open: true },
    })
    const penaltyRow = wrapper.find('.score-row-penalty')
    expect(penaltyRow.exists()).toBe(true)
    expect(penaltyRow.text()).toContain('Variety penalty')
    // Minus sign (U+2212).
    expect(penaltyRow.text()).toContain('−25%')
  })

  it('omits the variety penalty row when there is no penalty', () => {
    const wrapper = mount(RecScoreDetails, {
      props: { rec: makeRec({ variety_penalty: 0 }), open: true },
    })
    expect(wrapper.find('.score-row-penalty').exists()).toBe(false)
  })

  it('shows the breakdown details when only a variety penalty is present', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({ score_breakdown: {}, variety_penalty: 0.8 }),
        open: true,
      },
    })
    expect(wrapper.find('.score-details').exists()).toBe(true)
    expect(wrapper.find('.score-row-penalty').exists()).toBe(true)
  })

  it('reads every scorer as a percentage, never as a decimal out of one', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({ score: 0.88, score_breakdown: { genre_match: 0.88 } }),
        open: true,
      },
    })
    expect(wrapper.get('.score-value').text()).toBe('88%')
    expect(wrapper.text()).not.toContain('0.88')
  })

  it('shows a maxed-out signal at 100%, not at the slice of the total its weight allows', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({
          score: 0.57,
          score_breakdown: { series_order: 1, genre_match: 0.25 },
          scorer_weights: { series_order: 1.5, genre_match: 2 },
        }),
        open: true,
      },
    })

    expect(wrapper.findAll('.score-value').map((cell) => cell.text())).toEqual(['25%', '100%'])
    expect(wrapper.findAll('.score-bar-fill').map((bar) => bar.attributes('style'))).toEqual([
      'width: 25%;',
      'width: 100%;',
    ])
  })

  it('never claims the rows add up to the match, which per-signal values do not', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({ score: 0.7, score_breakdown: { tag_overlap: 0.9, continuation: 0.6 } }),
        open: true,
      },
    })

    expect(wrapper.findAll('.score-value').map((cell) => cell.text())).toEqual(['90%', '60%'])
    expect(wrapper.text()).not.toContain('70')
  })

  it('orders the signals by the weight the run gave them, not by how well the item did', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({
          score_breakdown: { tag_overlap: 0.9, continuation: 0.6 },
          scorer_weights: { tag_overlap: 1, continuation: 2 },
        }),
        open: true,
      },
    })

    expect(wrapper.findAll('.score-label').map((cell) => cell.text())).toEqual([
      'Continuation',
      'Tag Overlap',
    ])
    expect(wrapper.findAll('.score-value').map((cell) => cell.text())).toEqual(['60%', '90%'])
  })

  it('reads a scorer weighted to zero as off, never as the strength it would have had', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({
          score_breakdown: { tag_overlap: 0.9, genre_match: 0.4 },
          scorer_weights: { tag_overlap: 0, genre_match: 1 },
        }),
        open: true,
      },
    })

    expect(wrapper.findAll('.score-label').map((cell) => cell.text())).toEqual([
      'Genre Match',
      'Tag Overlap',
    ])
    expect(wrapper.findAll('.score-value').map((cell) => cell.text())).toEqual(['40%', 'Off'])
    expect(wrapper.findAll('.score-bar-fill').map((bar) => bar.attributes('style'))).toEqual([
      'width: 40%;',
      'width: 0%;',
    ])
  })

  it('is hidden from everyone, not just from sight, while collapsed', () => {
    const wrapper = mount(RecScoreDetails, { props: { rec: makeRec(), open: false } })

    expect(wrapper.get('.score-details').attributes('hidden')).toBeDefined()
  })
})
