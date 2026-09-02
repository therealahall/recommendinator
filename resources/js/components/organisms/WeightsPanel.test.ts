import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import WeightsPanel from './WeightsPanel.vue'
import { DEFAULT_WEIGHTS, usePreferencesStore } from '@/stores/preferences'
import type { UserPreferenceResponse } from '@/types/api'

const get = vi.fn()
const put = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({ get, put, delete: vi.fn() }),
}))

function stored(weights: Record<string, number> = {}, variety = 0): UserPreferenceResponse {
  return {
    scorer_weights: { ...DEFAULT_WEIGHTS, ...weights },
    series_in_order: true,
    variety_penalty: variety,
    content_length_preferences: {},
    custom_rules: [],
  } as UserPreferenceResponse
}

async function panel(preferences: UserPreferenceResponse) {
  get.mockResolvedValue(preferences)
  const wrapper = mount(WeightsPanel, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function trigger(wrapper: Awaited<ReturnType<typeof panel>>) {
  return wrapper.get('[data-testid="weights-trigger"]')
}

describe('WeightsPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    get.mockReset()
    put.mockReset()
    put.mockResolvedValue(undefined)
  })

  const CLOSED: Array<{ state: string; weights: Record<string, number>; variety: number; says: string }> = [
    { state: 'nothing has been tuned', weights: {}, variety: 0, says: 'all at their defaults' },
    { state: 'two signals have', weights: { genre_match: 4 }, variety: 2, says: '2 changed' },
  ]

  // Closed is not silent: a control reading only "Weights" leaves the screen
  // incomplete rather than complete without the panel open.
  it.each(CLOSED)('names what it holds while closed, when $state', async ({ weights, variety, says }) => {
    const wrapper = await panel(stored(weights, variety))

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(trigger(wrapper).text()).toContain('Scoring weights')
    expect(trigger(wrapper).text()).toContain(says)
    expect(trigger(wrapper).attributes('aria-expanded')).toBe('false')
  })

  it('opens a named dialog and puts the keyboard inside it', async () => {
    const wrapper = await panel(stored())

    await trigger(wrapper).trigger('click')
    await flushPromises()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(wrapper.get(`#${dialog.attributes('aria-labelledby')}`).text()).toContain(
      'Scoring weights',
    )
    expect(document.activeElement).toBe(dialog.element)
    wrapper.unmount()
  })

  // Escape has to leave the keyboard somewhere it can act, and the control that
  // summoned the panel is the only place it was (WCAG 2.4.3).
  it('closes on Escape and hands focus back to the control that summoned it', async () => {
    const wrapper = await panel(stored())
    ;(trigger(wrapper).element as HTMLElement).focus()
    await trigger(wrapper).trigger('click')
    await flushPromises()
    expect(document.activeElement).not.toBe(trigger(wrapper).element)

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger(wrapper).element)
    wrapper.unmount()
  })

  const DISMISSALS: Array<[string, (wrapper: Awaited<ReturnType<typeof panel>>) => unknown]> = [
    ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['the scrim behind it', (wrapper) => wrapper.get('.weights-scrim').trigger('click')],
    ['its close button', (wrapper) => wrapper.get('.weights-close').trigger('click')],
  ]

  // The sliders write to the shared store, so an abandoned weight rode along
  // with the next save made anywhere else.
  it.each(DISMISSALS)('puts back the weights it opened on when dismissed by %s', async (_label, dismiss) => {
    const wrapper = await panel(stored({ genre_match: 3 }))
    await trigger(wrapper).trigger('click')
    await flushPromises()
    const prefs = usePreferencesStore()
    prefs.setWeight('genre_match', 5)
    prefs.varietyPenalty = 4

    await dismiss(wrapper)
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(prefs.getWeight('genre_match')).toBe(3)
    expect(prefs.varietyPenalty).toBe(0)
    wrapper.unmount()
  })

  it.each(DISMISSALS)('keeps a weight that was saved before being dismissed by %s', async (_label, dismiss) => {
    const wrapper = await panel(stored({ genre_match: 3 }))
    await trigger(wrapper).trigger('click')
    await flushPromises()
    const prefs = usePreferencesStore()
    prefs.setWeight('genre_match', 4)
    await wrapper.get('.weights-actions .btn-primary').trigger('click')
    await flushPromises()
    expect(put).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ scorer_weights: expect.objectContaining({ genre_match: 4 }) }),
    )

    await dismiss(wrapper)
    await flushPromises()

    expect(prefs.getWeight('genre_match')).toBe(4)
    expect(prefs.isDirty).toBe(false)
    wrapper.unmount()
  })

  // A dangling aria-controls points at nothing, which assistive tech reports as
  // a broken relationship rather than a closed one.
  it('points at the panel only while the panel is in the tree', async () => {
    const wrapper = await panel(stored())
    expect(trigger(wrapper).attributes('aria-controls')).toBeUndefined()

    await trigger(wrapper).trigger('click')
    await flushPromises()

    const controls = trigger(wrapper).attributes('aria-controls')
    expect(wrapper.get(`#${controls}`).attributes('role')).toBe('dialog')
    wrapper.unmount()
  })
})
