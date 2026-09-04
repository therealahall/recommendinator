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

function stored(weights: Record<string, number> = {}): UserPreferenceResponse {
  return {
    scorer_weights: { ...DEFAULT_WEIGHTS, ...weights },
    series_in_order: true,
    variety_penalty: 0,
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

  it('is named while closed, and says it is closed', async () => {
    const wrapper = await panel(stored())

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(trigger(wrapper).text()).not.toBe('')
    expect(trigger(wrapper).attributes('aria-expanded')).toBe('false')
  })

  it('opens a named dialog and puts the keyboard inside it', async () => {
    const wrapper = await panel(stored())

    await trigger(wrapper).trigger('click')
    await flushPromises()

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(wrapper.get(`#${dialog.attributes('aria-labelledby')}`).text()).not.toBe('')
    expect(document.activeElement).toBe(dialog.element)
    wrapper.unmount()
  })

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

  it('closes on a second Escape from a tooltip button, once its tooltip is gone', async () => {
    const wrapper = await panel(stored())
    await trigger(wrapper).trigger('click')
    await flushPromises()
    const tip = wrapper.get('.scorer-tooltip-wrap')

    await tip.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    await tip.trigger('keydown', { key: 'Escape' })
    await flushPromises()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    wrapper.unmount()
  })

  const DISMISSALS: Array<[string, (wrapper: Awaited<ReturnType<typeof panel>>) => unknown]> = [
    ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['the scrim behind it', (wrapper) => wrapper.get('.weights-scrim').trigger('click')],
    ['its close button', (wrapper) => wrapper.get('.weights-close').trigger('click')],
    ['a route change taking it off the page', (wrapper) => wrapper.unmount()],
  ]

  it.each(DISMISSALS)('puts back the weights it opened on when dismissed by %s', async (_label, dismiss) => {
    const wrapper = await panel(stored({ genre_match: 3 }))
    await trigger(wrapper).trigger('click')
    await flushPromises()
    const prefs = usePreferencesStore()
    prefs.setWeight('genre_match', 5)
    prefs.varietyPenalty = 4

    await dismiss(wrapper)
    await flushPromises()

    expect(document.querySelector('[role="dialog"]')).toBeNull()
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
