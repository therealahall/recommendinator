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
  it('takes off the points the variety penalty cost, not the fraction it is', () => {
    const wrapper = mount(RecScoreDetails, {
      props: { rec: makeRec({ variety_penalty: 0.25 }), open: true },
    })
    const penaltyRow = wrapper.find('.score-row-penalty')
    expect(penaltyRow.exists()).toBe(true)
    expect(penaltyRow.text()).toContain('Variety penalty')
    // Minus sign (U+2212): a quarter off the 80 points the one scorer put in.
    expect(penaltyRow.text()).toContain('−20%')
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
    expect(wrapper.text()).toContain('How 88% was reached')
    expect(wrapper.text()).not.toContain('0.88')
  })

  it('weights each signal the way the run did, and accounts for the total it names', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({
          score: 0.7,
          score_breakdown: { tag_overlap: 0.9, continuation: 0.6 },
          scorer_weights: { tag_overlap: 1, continuation: 2 },
        }),
        open: true,
      },
    })

    expect(wrapper.text()).toContain('How 70% was reached')
    expect(wrapper.findAll('.score-label').map((cell) => cell.text())).toEqual([
      'Continuation',
      'Tag Overlap',
    ])
    expect(wrapper.findAll('.score-value').map((cell) => cell.text())).toEqual(['40%', '30%'])
  })

  it('is hidden from everyone, not just from sight, while collapsed', () => {
    const wrapper = mount(RecScoreDetails, { props: { rec: makeRec(), open: false } })

    expect(wrapper.get('.score-details').attributes('hidden')).toBeDefined()
  })
})
