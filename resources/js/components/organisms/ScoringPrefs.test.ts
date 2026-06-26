import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ScoringPrefs from './ScoringPrefs.vue'
import ScorerSlider from '@/components/atoms/ScorerSlider.vue'
import {
  usePreferencesStore,
  VARIETY_PENALTY_TOOLTIP,
} from '@/stores/preferences'
import { useAppStore } from '@/stores/app'

function labelStartsWith(wrapper: ReturnType<typeof mount>, text: string): boolean {
  return wrapper.findAll('.slider-label').some((labelEl) => labelEl.text().startsWith(text))
}

describe('ScoringPrefs', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders a single h3 titled "Scoring"', () => {
    const wrapper = mount(ScoringPrefs)

    const headings = wrapper.findAll('h3')
    expect(headings).toHaveLength(1)
    expect(headings[0].text()).toBe('Scoring')
  })

  it('does not render the old "Scorer Weights" or "Toggles" headings', () => {
    const wrapper = mount(ScoringPrefs)

    expect(wrapper.text()).not.toContain('Scorer Weights')
    expect(wrapper.text()).not.toContain('Toggles')
  })

  it('renders 8 scorer sliders plus 1 variety slider by default (AI off)', () => {
    const wrapper = mount(ScoringPrefs)

    // 8 scorer sliders (semantic_similarity is gated off when AI is disabled)
    // plus 1 variety slider = 9.
    const sliders = wrapper.findAll('input[type="range"]')
    expect(sliders).toHaveLength(9)
    // Every slider now uses the shared 0–5 ScorerSlider scale.
    expect(sliders.every((s) => s.attributes('max') === '5')).toBe(true)
  })

  it('shows the semantic_similarity slider only when AI + embeddings are on', () => {
    expect(labelStartsWith(mount(ScoringPrefs), 'Semantic Similarity')).toBe(false)

    const app = useAppStore()
    app.features.ai_enabled = true
    app.features.embeddings_enabled = true
    expect(labelStartsWith(mount(ScoringPrefs), 'Semantic Similarity')).toBe(true)
  })

  it('renders the variety slider as a 0–5 ScorerSlider after the scorer weights', () => {
    const wrapper = mount(ScoringPrefs)

    // The variety slider is the last ScorerSlider; assert its label prop exactly
    // so a longer string like "Variety After Completion Strength" would fail.
    const scorerSliders = wrapper.findAllComponents(ScorerSlider)
    const varietySlider = scorerSliders[scorerSliders.length - 1]
    expect(varietySlider.props('label')).toBe('Variety After Completion')

    const sliders = wrapper.findAll('input[type="range"]')
    const variety = sliders[sliders.length - 1]
    expect(variety.attributes('max')).toBe('5')
    expect(variety.attributes('aria-valuetext')).toBe('0.0')

    // It renders after the scorer-weight sliders.
    const lastWeight = sliders[sliders.length - 2]
    const position = lastWeight.element.compareDocumentPosition(variety.element)
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('keeps the variety tooltip in the accessibility tree', () => {
    const wrapper = mount(ScoringPrefs)

    const tooltips = wrapper.findAll('[role="tooltip"]').map((t) => t.text())
    expect(tooltips).toContain(VARIETY_PENALTY_TOOLTIP)
  })

  it('writes the raw 0–5 float to varietyPenalty on input (no /100 scaling)', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(ScoringPrefs)

    const sliders = wrapper.findAll('input[type="range"]')
    const variety = sliders[sliders.length - 1]
    ;(variety.element as HTMLInputElement).value = '3.5'
    await variety.trigger('input')

    expect(prefs.varietyPenalty).toBe(3.5)
  })

  it('writes a scorer weight back to the store on input', async () => {
    const prefs = usePreferencesStore()
    const wrapper = mount(ScoringPrefs)

    // The first slider is genre_match, the first SCORER_KEYS entry.
    const input = wrapper.findAll('input[type="range"]')[0]
    ;(input.element as HTMLInputElement).value = '4.2'
    await input.trigger('input')

    expect(prefs.getWeight('genre_match')).toBe(4.2)
  })
})
