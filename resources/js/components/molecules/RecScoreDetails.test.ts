import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecScoreDetails from './RecScoreDetails.vue'
import type { RecommendationResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 1,
    title: 'Test',
    author: 'Author',
    score: 0.5,
    similarity_score: 0,
    preference_score: 0,
    reasoning: 'Because',
    llm_reasoning: null,
    score_breakdown: { genre_match: 0.8 },
    variety_penalty: 0,
    ...overrides,
  }
}

describe('RecScoreDetails', () => {
  it('renders the variety penalty row with a negative percentage label', () => {
    const wrapper = mount(RecScoreDetails, {
      props: { rec: makeRec({ variety_penalty: 0.64 }), defaultOpen: true },
    })
    const penaltyRow = wrapper.find('.score-row-penalty')
    expect(penaltyRow.exists()).toBe(true)
    expect(penaltyRow.text()).toContain('Variety penalty')
    // Minus sign (U+2212) plus the rounded percentage.
    expect(penaltyRow.text()).toContain('−64%')
    const fill = penaltyRow.find('.score-bar-fill-penalty')
    expect(fill.attributes('style')).toContain('width: 64%')
  })

  it('omits the variety penalty row when there is no penalty', () => {
    const wrapper = mount(RecScoreDetails, {
      props: { rec: makeRec({ variety_penalty: 0 }), defaultOpen: true },
    })
    expect(wrapper.find('.score-row-penalty').exists()).toBe(false)
  })

  it('shows the breakdown details when only a variety penalty is present', () => {
    const wrapper = mount(RecScoreDetails, {
      props: {
        rec: makeRec({ score_breakdown: {}, variety_penalty: 0.8 }),
        defaultOpen: true,
      },
    })
    expect(wrapper.find('.score-details').exists()).toBe(true)
    expect(wrapper.find('.score-row-penalty').exists()).toBe(true)
  })
})
