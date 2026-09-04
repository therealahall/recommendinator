import { describe, it, expect, beforeEach } from 'vitest'
import { mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ScoringPrefs from './ScoringPrefs.vue'
import ScorerSlider from '@/components/atoms/ScorerSlider.vue'
import {
  usePreferencesStore,
  SCORER_KEYS,
  SCORER_TOOLTIPS,
  VARIETY_PENALTY_TOOLTIP,
} from '@/stores/preferences'

function sliderFor(wrapper: VueWrapper, tooltip: string): DOMWrapper<Element> {
  const slider = wrapper.findAllComponents(ScorerSlider).find((s) => s.props('tooltip') === tooltip)
  if (!slider) throw new Error(`no slider is described by "${tooltip}"`)
  return slider.find('input[type="range"]')
}

describe('ScoringPrefs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders one slider per scorer plus the variety slider', () => {
    const wrapper = mount(ScoringPrefs)

    const sliders = wrapper.findAll('input[type="range"]')
    expect(sliders).toHaveLength(SCORER_KEYS.length + 1)
    expect(sliders.every((s) => s.attributes('max') === '5')).toBe(true)
  })

  it('gives the variety slider the 0–5 scale and announces its value as a number', () => {
    const wrapper = mount(ScoringPrefs)

    const variety = sliderFor(wrapper, VARIETY_PENALTY_TOOLTIP)
    expect(variety.attributes('max')).toBe('5')
    expect(variety.attributes('aria-valuetext')).toBe('0.0')
  })

  it('keeps the variety tooltip in the accessibility tree', () => {
    const wrapper = mount(ScoringPrefs)

    const tooltips = wrapper.findAll('[role="tooltip"]').map((t) => t.text())
    expect(tooltips).toContain(VARIETY_PENALTY_TOOLTIP)
  })

  it('writes the raw 0–5 float to varietyPenalty on input (no /100 scaling)', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(ScoringPrefs)

    const variety = sliderFor(wrapper, VARIETY_PENALTY_TOOLTIP)
    ;(variety.element as HTMLInputElement).value = '3.5'
    await variety.trigger('input')

    expect(prefs.varietyPenalty).toBe(3.5)
  })

  it('writes each scorer slider back to the weight it describes, not a neighbour', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(ScoringPrefs)

    for (let index = 0; index < SCORER_KEYS.length; index += 1) {
      const key = SCORER_KEYS[index]
      const value = (index + 1) * 0.5
      const input = sliderFor(wrapper, SCORER_TOOLTIPS[key])
      ;(input.element as HTMLInputElement).value = String(value)
      await input.trigger('input')

      expect(prefs.getWeight(key), `the ${key} slider wrote elsewhere`).toBe(value)
    }
  })
})
