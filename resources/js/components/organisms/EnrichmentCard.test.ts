import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EnrichmentCard from './EnrichmentCard.vue'
import { useDataStore } from '@/stores/data'
import type { EnrichmentStatsResponse, EnrichmentJobStatusResponse } from '@/types/api'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

enableAutoUnmount(afterEach)

function makeStats(overrides: Partial<EnrichmentStatsResponse> = {}): EnrichmentStatsResponse {
  return {
    enabled: true,
    total: 100,
    resettable: 90,
    enriched: 50,
    pending: 45,
    not_found: 5,
    failed: 0,
    by_provider: {},
    by_quality: {},
    ...overrides,
  }
}

function makeRunningJob(overrides: Partial<EnrichmentJobStatusResponse> = {}): EnrichmentJobStatusResponse {
  return {
    running: true,
    completed: false,
    cancelled: false,
    items_processed: 25,
    items_enriched: 20,
    items_failed: 0,
    items_not_found: 5,
    total_items: 50,
    current_item: 'Processing item...',
    content_type: null,
    errors: [],
    elapsed_seconds: 10,
    progress_percent: 50,
    ...overrides,
  }
}

describe('EnrichmentCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function mountWithEnrichment(overrides: Partial<ReturnType<typeof useDataStore>> = {}) {
    const data = useDataStore()
    data.enrichmentEnabled = true
    data.enrichmentStats = makeStats()
    data.enrichmentJob = null
    Object.assign(data, overrides)
    return mount(EnrichmentCard, { attachTo: document.body })
  }

  it('offers the setup state on a fresh install rather than hiding the card', async () => {
    const data = useDataStore()
    data.enrichmentEnabled = false
    data.enableEnrichment = vi.fn().mockResolvedValue(undefined)
    const wrapper = mount(EnrichmentCard)

    expect(wrapper.find('[data-testid="enrichment-setup"]').exists()).toBe(true)
    await wrapper.find('[data-testid="enrichment-enable"]').trigger('click')
    await flushPromises()

    expect(data.enableEnrichment).toHaveBeenCalled()
  })

  it('shows job progress when enrichment is running', () => {
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({
        progress_percent: 60,
        items_processed: 30,
        total_items: 50,
        current_item: 'Processing Dune',
      }),
    })

    expect(wrapper.find('.enrichment-status').exists()).toBe(true)
    expect(wrapper.text()).toContain('Processing Dune')
    expect(wrapper.text()).toContain('30/50')
    expect(wrapper.text()).toContain('60%')
  })

  it('calls startEnrichment with selected type and retry flag', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.startEnrichment = vi.fn().mockResolvedValue('Enrichment started.')

    const moviePill = wrapper.findAll('.pill').find((p) => p.text() === 'Movie')!
    await moviePill.trigger('click')
    await wrapper.find('.toggle-switch').trigger('click')
    await wrapper.find('[data-testid="enrichment-start"]').trigger('click')

    expect(data.startEnrichment).toHaveBeenCalledWith('movie', true)
  })

  it('says a refused start instead of leaving the button looking dead', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.startEnrichment = vi.fn().mockRejectedValue(new Error('Enrichment is disabled'))

    await wrapper.find('[data-testid="enrichment-start"]').trigger('click')
    await flushPromises()

    const alert = wrapper.get('[data-testid="enrichment-error"]')
    expect(alert.text()).toContain('Enrichment is disabled')
    expect(document.activeElement).toBe(alert.element)
  })

  it('confirms a start that queued nothing, so the click has an outcome', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.startEnrichment = vi.fn().mockResolvedValue('Nothing to enrich.')

    await wrapper.find('[data-testid="enrichment-start"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="enrichment-message"]').text()).toBe(
      'Nothing to enrich.',
    )
  })

  it('offers Stop only while a job is running, and stops it', async () => {
    const idle = mountWithEnrichment()
    expect(idle.find('[data-testid="enrichment-stop"]').exists()).toBe(false)

    const wrapper = mountWithEnrichment({ enrichmentJob: makeRunningJob() })
    const data = useDataStore()
    data.stopEnrichment = vi.fn().mockResolvedValue('Enrichment stopped.')

    await wrapper.find('[data-testid="enrichment-stop"]').trigger('click')
    await flushPromises()

    expect(data.stopEnrichment).toHaveBeenCalled()
  })

  it('tells a failed stats read apart from a library with nothing to enrich', () => {
    const wrapper = mountWithEnrichment({
      enrichmentStatsError: 'backend is down',
      enrichmentStats: null,
    })

    expect(wrapper.text()).toContain('backend is down')
    expect(wrapper.text()).not.toContain('No items to enrich')
  })

  describe('reset', () => {
    it('asks before re-queueing, naming the count and the scope', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn()

      await wrapper.find('[data-testid="reset-btn"]').trigger('click')

      const question = wrapper.get('[data-testid="confirm-panel"]').text()
      expect(data.resetEnrichment).not.toHaveBeenCalled()
      expect(question).toContain('90 item(s)')
      expect(question).toContain('every content type')
    })

    it('sends no provider filter on the default, which is not a provider name', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn().mockResolvedValue('Reset 55 item(s).')

      await wrapper.find('[data-testid="reset-btn"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      // 'all' reaching the API matches enrichment_provider = 'all', which is
      // no row: the default reset silently did nothing.
      expect(data.resetEnrichment).toHaveBeenCalledWith(undefined, '')
    })

    it('puts the keyboard back on Reset once the question is answered', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn().mockResolvedValue('Reset 1 item(s).')

      const button = wrapper.get('[data-testid="reset-btn"]')
      ;(button.element as HTMLElement).focus()
      await button.trigger('click')
      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      expect(document.activeElement).toBe(button.element)
    })

    it('sends the provider filter the CLI offers', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn().mockResolvedValue('Reset 12 item(s).')

      await wrapper.findAll('.pill').find((p) => p.text() === 'Movie')!.trigger('click')
      await wrapper.find('[data-testid="reset-provider"]').setValue('rawg')
      await wrapper.find('[data-testid="reset-btn"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-confirm"]').trigger('click')
      await flushPromises()

      expect(data.resetEnrichment).toHaveBeenCalledWith('movie', 'rawg')
    })

    it('keeps the keyboard on the button it disables while the request runs', async () => {
      // `disabled` blurs the control the user just pressed; aria-disabled does
      // not, and the guard in run() is what stops a second activation.
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.startEnrichment = vi.fn().mockResolvedValue('Started.')

      const button = wrapper.get('[data-testid="enrichment-start"]')
      ;(button.element as HTMLElement).focus()
      await button.trigger('click')
      await flushPromises()

      expect(button.attributes('disabled')).toBeUndefined()
      expect(document.activeElement).toBe(button.element)
    })

    it('leaves the items alone when the question is declined', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn()

      const button = wrapper.get('[data-testid="reset-btn"]')
      // jsdom does not focus on click the way a browser does.
      ;(button.element as HTMLElement).focus()
      await button.trigger('click')
      await wrapper.find('[data-testid="confirm-panel-cancel"]').trigger('click')
      await flushPromises()

      expect(data.resetEnrichment).not.toHaveBeenCalled()
      expect(document.activeElement).toBe(button.element)
    })
  })
})
