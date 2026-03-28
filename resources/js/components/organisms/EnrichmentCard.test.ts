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

  it('renders when enrichment is enabled', () => {
    const wrapper = mountWithEnrichment()
    expect(wrapper.find('.card').exists()).toBe(true)
    expect(wrapper.text()).toContain('Metadata Enrichment')
  })

  it('does not render when enrichment is disabled', () => {
    const data = useDataStore()
    data.enrichmentEnabled = false
    const wrapper = mount(EnrichmentCard)
    expect(wrapper.find('.card').exists()).toBe(false)
  })

  it('does not render stats when enrichmentStats is null', () => {
    const wrapper = mountWithEnrichment({ enrichmentStats: null })
    expect(wrapper.find('.enrichment-summary').exists()).toBe(false)
    expect(wrapper.find('.empty-state').exists()).toBe(false)
  })

  it('shows empty state when total is zero', () => {
    const wrapper = mountWithEnrichment({
      enrichmentStats: makeStats({ total: 0, enriched: 0, pending: 0, not_found: 0 }),
    })
    expect(wrapper.find('.empty-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('No items to enrich')
  })

  it('renders TypePills with All option', () => {
    const wrapper = mountWithEnrichment()

    const pills = wrapper.findAll('.pill')
    expect(pills.map(p => p.text())).toEqual(['All', 'Book', 'Movie', 'TV Show', 'Game'])
  })

  it('renders All pill as active by default', () => {
    const wrapper = mountWithEnrichment()

    const allPill = wrapper.findAll('.pill').find(p => p.text() === 'All')!
    expect(allPill.classes()).toContain('active')
  })

  it('switches active pill on click', async () => {
    const wrapper = mountWithEnrichment()

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    expect(moviePill.classes()).toContain('active')
    const allPill = wrapper.findAll('.pill').find(p => p.text() === 'All')!
    expect(allPill.classes()).not.toContain('active')
  })

  it('renders Retry Not Found toggle switch', () => {
    const wrapper = mountWithEnrichment()

    expect(wrapper.find('.toggle-switch').exists()).toBe(true)
    expect(wrapper.text()).toContain('Retry Not Found')
  })

  it('renders Enrich and Reset buttons', () => {
    const wrapper = mountWithEnrichment()

    const buttons = wrapper.findAll('.btn')
    expect(buttons.map(b => b.text())).toContain('Enrich')
    expect(buttons.map(b => b.text())).toContain('Reset & Re-enrich')
  })

  it('disables buttons when job is running', () => {
    const wrapper = mountWithEnrichment({ enrichmentJob: makeRunningJob() })

    const enrichBtn = wrapper.findAll('.btn').find(b => b.text() === 'Enrich')!
    const resetBtn = wrapper.findAll('.btn').find(b => b.text() === 'Reset & Re-enrich')!
    expect(enrichBtn.attributes('disabled')).toBeDefined()
    expect(resetBtn.attributes('disabled')).toBeDefined()
  })

  it('shows Processing... fallback when current_item is empty', () => {
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({ current_item: '' }),
    })

    expect(wrapper.find('.enrichment-status').exists()).toBe(true)
    expect(wrapper.text()).toContain('Processing...')
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

  it('shows enrichment progress stats', () => {
    const wrapper = mountWithEnrichment()
    expect(wrapper.text()).toContain('50/100')
    expect(wrapper.text()).toContain('50% enriched')
  })

  it('shows non-round percentage correctly', () => {
    const wrapper = mountWithEnrichment({
      enrichmentStats: makeStats({ total: 3, enriched: 1, pending: 2 }),
    })
    expect(wrapper.text()).toContain('1/3')
    expect(wrapper.text()).toContain('33% enriched')
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

  it('calls startEnrichment with undefined when All type is selected', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.startEnrichment = vi.fn()

    const enrichBtn = wrapper.findAll('.btn').find(b => b.text() === 'Enrich')!
    await enrichBtn.trigger('click')

    expect(data.startEnrichment).toHaveBeenCalledWith(undefined, false)
  })

  it('calls resetEnrichment with selected type on Reset button click', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.resetEnrichment = vi.fn()

    const moviePill = wrapper.findAll('.pill').find(p => p.text() === 'Movie')!
    await moviePill.trigger('click')

    const resetBtn = wrapper.findAll('.btn').find(b => b.text() === 'Reset & Re-enrich')!
    await resetBtn.trigger('click')

    expect(data.resetEnrichment).toHaveBeenCalledWith('movie')
  })

  it('calls resetEnrichment with undefined when All type is selected', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    data.resetEnrichment = vi.fn()

    const resetBtn = wrapper.findAll('.btn').find(b => b.text() === 'Reset & Re-enrich')!
    await resetBtn.trigger('click')

    expect(data.resetEnrichment).toHaveBeenCalledWith(undefined)
  })
})
