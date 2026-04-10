import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RecControls from './RecControls.vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import { useAppStore } from '@/stores/app'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

vi.mock('@/composables/useSse', () => ({
  readSseStream: vi.fn(),
}))

describe('RecControls', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders TypePills without All option', () => {
    const wrapper = mount(RecControls)

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['Book', 'Movie', 'TV Show', 'Game'])
  })

  it('marks Book pill as active by default', () => {
    const wrapper = mount(RecControls)

    const bookPill = wrapper.findAll('.pill').find(p => p.text() === 'Book')!
    expect(bookPill.classes()).toContain('active')
  })

  it('updates content type on pill click', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    expect(recs.contentType).toBe('movie')
  })

  it('renders a single count input with default value', () => {
    const wrapper = mount(RecControls)

    const inputs = wrapper.findAll('input[type="number"]')
    expect(inputs).toHaveLength(1)
    expect((inputs[0].element as HTMLInputElement).value).toBe('5')
  })

  it('sets max attribute on count input from recommendationsConfig', () => {
    const app = useAppStore()
    app.recommendationsConfig.max_count = 50

    const wrapper = mount(RecControls)

    const input = wrapper.find('input[type="number"]')
    expect(input.attributes('max')).toBe('50')
  })

  it('updates recs.count when input changes', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const input = wrapper.find('input[type="number"]')
    await input.setValue('10')

    expect(recs.count).toBe(10)
  })

  it('NumberStepper receives aria-label for accessible name', () => {
    const wrapper = mount(RecControls)

    const input = wrapper.find('input[type="number"]')
    expect(input.attributes('aria-label')).toBe('Number of recommendations')

    const decBtn = wrapper.find('.stepper-decrement')
    expect(decBtn.attributes('aria-label')).toBe('Decrease Number of recommendations')

    const incBtn = wrapper.find('.stepper-increment')
    expect(incBtn.attributes('aria-label')).toBe('Increase Number of recommendations')
  })

  it('renders Generate button', () => {
    const wrapper = mount(RecControls)

    const genBtn = wrapper.findAll('.btn').find(b => b.text() === 'Generate')!
    expect(genBtn.exists()).toBe(true)
  })

  it('hides AI Recommendations button when AI reasoning is disabled', () => {
    const wrapper = mount(RecControls)

    expect(wrapper.text()).not.toContain('AI Recommendations')
  })

  it('shows AI Recommendations button when AI reasoning is enabled', () => {
    const app = useAppStore()
    app.features.ai_enabled = true
    app.features.llm_reasoning_enabled = true

    const wrapper = mount(RecControls)

    const aiBtn = wrapper.findAll('.btn').find(b => b.text().includes('AI'))
    expect(aiBtn).toBeDefined()
  })

  it('disables buttons when loading', () => {
    const recs = useRecommendationsStore()
    recs.loading = true

    const app = useAppStore()
    app.features.ai_enabled = true
    app.features.llm_reasoning_enabled = true

    const wrapper = mount(RecControls)

    const genBtn = wrapper.findAll('.btn').find(b => b.text() === 'Generate')!
    expect(genBtn.attributes('disabled')).toBeDefined()

    const aiBtn = wrapper.findAll('.btn').find(b => b.text().includes('AI'))!
    expect(aiBtn.attributes('disabled')).toBeDefined()
  })

  it('calls fetch with false on Generate click', async () => {
    const recs = useRecommendationsStore()
    recs.fetch = vi.fn()

    const wrapper = mount(RecControls)

    const genBtn = wrapper.findAll('.btn').find(b => b.text() === 'Generate')!
    await genBtn.trigger('click')

    expect(recs.fetch).toHaveBeenCalledWith(false)
  })

  it('calls fetch with true on AI Recommendations click', async () => {
    const recs = useRecommendationsStore()
    recs.fetch = vi.fn()

    const app = useAppStore()
    app.features.ai_enabled = true
    app.features.llm_reasoning_enabled = true

    const wrapper = mount(RecControls)

    const aiBtn = wrapper.findAll('.btn').find(b => b.text().includes('AI'))!
    await aiBtn.trigger('click')

    expect(recs.fetch).toHaveBeenCalledWith(true)
  })

  it('renders TypeSelect for mobile with no All option', () => {
    const wrapper = mount(RecControls)

    const select = wrapper.find('.rec-type-select')
    expect(select.exists()).toBe(true)

    const options = select.findAll('option')
    expect(options.map(o => o.text().trim())).toEqual(['Book', 'Movie', 'TV Show', 'Game'])
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

  it('TypeSelect reflects contentType after pill click', async () => {
    const wrapper = mount(RecControls)

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    const el = wrapper.find('.rec-type-select').element as HTMLSelectElement
    expect(el.value).toBe('movie')
  })

  it('reflects store contentType changes in TypeSelect', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    recs.contentType = 'tv_show'
    await wrapper.vm.$nextTick()

    const el = wrapper.find('.rec-type-select').element as HTMLSelectElement
    expect(el.value).toBe('tv_show')
  })
})
