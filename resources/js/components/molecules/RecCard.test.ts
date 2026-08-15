import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RecCard from './RecCard.vue'
import type { RecommendationResponse } from '@/types/api'

function makeRec(overrides: Partial<RecommendationResponse> = {}): RecommendationResponse {
  return {
    db_id: 7,
    title: 'Test',
    author: 'Author',
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

  it('omits the action buttons when there is no db_id', () => {
    const wrapper = mount(RecCard, {
      props: { rec: makeRec({ db_id: null }), rank: 1 },
    })
    expect(wrapper.find('.btn-complete').exists()).toBe(false)
    expect(wrapper.find('.btn-ignore').exists()).toBe(false)
  })
})

describe('RecCard header regression (issue #98)', () => {
  /**
   * Bug: at 375px the badge and buttons squeezed the title. Root cause: the
   * title sat in an unclassed div, so the mobile rule had nothing to select.
   * Fix: .rec-heading, the hook base.css gives the full row.
   */
  it('wraps the title in .rec-heading, a sibling of .rec-actions', () => {
    const wrapper = mount(RecCard, { props: { rec: makeRec(), rank: 1 } })

    const heading = wrapper.find('.rec-heading')
    const actions = wrapper.find('.rec-actions')
    expect(heading.find('.rec-title').exists()).toBe(true)
    expect(heading.element.parentElement).toBe(actions.element.parentElement)
    expect(heading.element.parentElement?.classList.contains('rec-header')).toBe(true)
  })

  it('orders the title before the actions, so the wrapped row lands beneath it', () => {
    const wrapper = mount(RecCard, { props: { rec: makeRec(), rank: 1 } })

    const children = [...wrapper.find('.rec-header').element.children]
    expect(children.map((el) => el.className)).toEqual(['rec-heading', 'rec-actions'])
  })

  it('keeps the score badge with the buttons rather than beside the title', () => {
    const wrapper = mount(RecCard, { props: { rec: makeRec({ score: 0.42 }), rank: 1 } })

    const badge = wrapper.find('.badge-score')
    expect(badge.text()).toBe('0.42')
    expect(badge.element.parentElement?.classList.contains('rec-actions')).toBe(true)
  })
})
