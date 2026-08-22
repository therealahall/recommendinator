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

  it('nests under the Data page heading rather than beside it', () => {
    const wrapper = mountWithEnrichment()

    expect(wrapper.find('h2').exists()).toBe(false)
    expect(wrapper.get('h3').text()).toBe('Metadata Enrichment')
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
    it('is its own button, not a mode that rewrites Enrich', () => {
      const wrapper = mountWithEnrichment()

      expect(wrapper.get('[data-testid="enrichment-start"]').text()).toBe('Enrich')
      expect(wrapper.get('[data-testid="reset-btn"]').text()).toBe('Reset enrichment')
    })

    it('asks before re-queueing, naming the scope and the item count', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn()

      await wrapper.find('[data-testid="reset-btn"]').trigger('click')

      expect(data.resetEnrichment).not.toHaveBeenCalled()
      const question = wrapper.get('[data-testid="confirm-panel"]').text()
      expect(question).toContain('55 item(s)')
      expect(question).toContain('every content type')
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

    it('leaves the items alone when the question is declined', async () => {
      const wrapper = mountWithEnrichment()
      const data = useDataStore()
      data.resetEnrichment = vi.fn()

      await wrapper.find('[data-testid="reset-btn"]').trigger('click')
      await wrapper.find('[data-testid="confirm-panel-cancel"]').trigger('click')
      await flushPromises()

      expect(data.resetEnrichment).not.toHaveBeenCalled()
      expect(document.activeElement).toBe(
        wrapper.get('[data-testid="reset-btn"]').element,
      )
    })
  })
})
