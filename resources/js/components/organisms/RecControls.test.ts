import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RecControls from './RecControls.vue'
import { useRecommendationsStore } from '@/stores/recommendations'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

describe('RecControls', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('updates content type on pill click', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    expect(recs.contentType).toBe('movie')
  })

  it('updates recs.count when input changes', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const input = wrapper.find('input[type="number"]')
    await input.setValue('10')

    expect(recs.count).toBe(10)
  })

  it('calls fetch on Generate click', async () => {
    const recs = useRecommendationsStore()
    recs.fetch = vi.fn()

    const wrapper = mount(RecControls)

    const genBtn = wrapper.findAll('.btn').find(b => b.text() === 'Generate')!
    await genBtn.trigger('click')

    expect(recs.fetch).toHaveBeenCalled()
  })

  it('updates content type from TypeSelect', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const select = wrapper.find('.rec-type-select')
    const el = select.element as HTMLSelectElement
    el.value = 'movie'
    await select.trigger('change')

    expect(recs.contentType).toBe('movie')
  })
})

describe('RecControls layout regression (issue #58)', () => {
  /**
   * Bug: on mobile (≤640px), the NumberStepper rendered on its own row below
   * the content-type dropdown instead of on the same line.
   *
   * Root cause: NumberStepper was nested inside .rec-actions-row, which has
   * width: 100% on mobile, forcing it (and its parent row) below the dropdown.
   *
   * Fix: lift NumberStepper to be a direct child of .rec-toolbar alongside
   * TypePills/TypeSelect. Mobile CSS lets dropdown + stepper share the top row
   * while .toolbar-actions wraps to its own full-width row below.
   *
   * These tests assert the structural invariant the CSS fix relies on:
   * NumberStepper is a sibling of TypeSelect inside .rec-toolbar, and the
   * action buttons sit in a separate .toolbar-actions wrapper that can wrap
   * independently.
   */
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('NumberStepper and TypeSelect are siblings under .rec-toolbar', () => {
    const wrapper = mount(RecControls)

    const stepper = wrapper.find('.number-stepper')
    const select = wrapper.find('.rec-type-select')
    expect(stepper.exists()).toBe(true)
    expect(select.exists()).toBe(true)

    const stepperParent = stepper.element.parentElement
    const selectParent = select.element.parentElement
    expect(stepperParent?.classList.contains('rec-toolbar')).toBe(true)
    expect(selectParent?.classList.contains('rec-toolbar')).toBe(true)
    expect(stepperParent).toBe(selectParent)
  })

  it('action buttons live in .toolbar-actions which is a direct child of .rec-toolbar', () => {
    const wrapper = mount(RecControls)

    const genBtn = wrapper.findAll('.btn').find(b => b.text() === 'Generate')
    expect(genBtn).toBeDefined()

    const actions = genBtn!.element.parentElement
    expect(actions).not.toBeNull()
    expect(actions!.classList.contains('toolbar-actions')).toBe(true)
    expect(actions!.parentElement?.classList.contains('rec-toolbar')).toBe(true)
  })
})
