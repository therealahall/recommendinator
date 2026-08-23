import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecCard from './RecCard.vue'
import type { RecommendationResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 7,
    title: 'Test',
    author: 'Author',
    series: null,
    series_index: null,
    score: 0.5,
    reasoning: 'Because',
    score_breakdown: {},
    variety_penalty: 0,
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
})
