import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
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
    return mount(EnrichmentCard)
  }

  it('does not render when enrichment is disabled', () => {
    const data = useDataStore()
    data.enrichmentEnabled = false
    const wrapper = mount(EnrichmentCard)
    expect(wrapper.find('.card').exists()).toBe(false)
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
    data.startEnrichment = vi.fn()

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    await wrapper.find('.toggle-switch').trigger('click')

    const enrichBtn = wrapper.findAll('.btn').find(b => b.text() === 'Enrich')!
    await enrichBtn.trigger('click')

    expect(data.startEnrichment).toHaveBeenCalledWith('movie', true)
  })

  it('calls resetEnrichment when reset toggle is on and button clicked', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.resetEnrichment = vi.fn()

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    // Toggle reset mode on (second toggle switch — first is Retry Not Found)
    const toggles = wrapper.findAll('.toggle-switch')
    const resetToggle = toggles[toggles.length - 1]
    await resetToggle.trigger('click')

    const actionBtn = wrapper.findAll('.btn').find(b => b.text() === 'Reset Enrichment')!
    await actionBtn.trigger('click')

    expect(data.resetEnrichment).toHaveBeenCalledWith('movie')
  })
})
