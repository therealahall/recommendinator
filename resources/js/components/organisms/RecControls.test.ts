import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RecControls from './RecControls.vue'
import { useRecommendationsStore } from '@/stores/recommendations'

const mockGet = vi.fn()

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
  }),
}))

describe('RecControls', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
  })

  it('updates content type on pill click', async () => {
    const recs = useRecommendationsStore()
    const wrapper = mount(RecControls)

    const moviePill = wrapper.findAll('[role="radio"]').find(p => p.text() === 'Movie')!
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

  it('says a generation is running from the Generate button the keyboard is still on', async () => {
    mockGet.mockImplementation(() => new Promise(() => {}))
    const wrapper = mount(RecControls, { attachTo: document.body })
    const button = wrapper.get('[data-testid="generate-btn"]')
    const idle = button.text()
    ;(button.element as HTMLButtonElement).focus()

    await button.trigger('click')
    await flushPromises()

    expect(button.attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(button.element)
    expect(button.text()).not.toBe(idle)
    wrapper.unmount()
  })

  it('asks for one set of recommendations when Generate is activated twice in flight', async () => {
    mockGet.mockImplementation(() => new Promise(() => {}))
    const wrapper = mount(RecControls)
    const button = wrapper.get('[data-testid="generate-btn"]')

    await button.trigger('click')
    await button.trigger('click')

    expect(mockGet).toHaveBeenCalledTimes(1)
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
