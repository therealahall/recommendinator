import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
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

  it('mounts the progress region before the first tick, not along with it', () => {
    const wrapper = mountWithEnrichment()

    const region = wrapper.get('[data-testid="enrichment-progress-status"]')
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('')
    // The visible detail is the sighted operator's; a live copy of it reads the
    // title and counts aloud again on every poll.
    expect(wrapper.get('.enrichment-status').attributes('aria-live')).toBeUndefined()
  })

  it('says nothing across polls that cross no milestone, and speaks once when one does', async () => {
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({ progress_percent: 30 }),
    })
    const data = useDataStore()
    const region = wrapper.get('[data-testid="enrichment-progress-status"]')
    const inside = region.text()

    for (const progress_percent of [31, 32, 33, 34, 35]) {
      data.enrichmentJob = makeRunningJob({
        progress_percent,
        current_item: `Item ${progress_percent}`,
      })
      await nextTick()
      expect(region.text()).toBe(inside)
    }

    data.enrichmentJob = makeRunningJob({ progress_percent: 60 })
    await nextTick()
    const crossed = region.text()
    expect(crossed).not.toBe(inside)

    data.enrichmentJob = makeRunningJob({ progress_percent: 61 })
    await nextTick()
    expect(region.text()).toBe(crossed)
  })

  it('announces the end of a run it saw, not a result stored by one it did not', async () => {
    // The job row keeps `completed` set indefinitely, so every later visit to
    // the page would otherwise report last week's run as news.
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({ running: false, completed: true }),
    })
    const data = useDataStore()
    const region = wrapper.get('[data-testid="enrichment-progress-status"]')
    expect(region.text()).toBe('')

    data.enrichmentJob = makeRunningJob()
    await nextTick()
    data.enrichmentJob = makeRunningJob({ running: false, completed: true })
    await nextTick()

    expect(region.text()).toContain('finished')
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

    const moviePill = wrapper.findAll('[role="radio"]').find((p) => p.text() === 'Movie')!
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

  it('shows why a run stopped, which the card used to drop entirely', () => {
    const abandoned = 'tmdb: abandoned for this run after 5 consecutive rejections'
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({
        running: false,
        completed: true,
        errors: ['tmdb: HTTP 401', abandoned],
      }),
    })

    expect(wrapper.get('[data-testid="enrichment-errors"]').text()).toContain(abandoned)
  })

  it('keeps the list semantics VoiceOver strips from a markerless list', () => {
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({ errors: ['tmdb: HTTP 401'] }),
    })

    expect(wrapper.get('[data-testid="enrichment-errors"]').attributes('role')).toBe('list')
  })

  it('announces errors that only ever arrive on a poll, long after the click', async () => {
    const wrapper = mountWithEnrichment()
    const data = useDataStore()
    const region = wrapper.get('[data-testid="enrichment-errors-status"]')

    // A region inserted already populated reads as content, and `v-show` would
    // take it out of the accessibility tree entirely (WCAG 4.1.3).
    expect(region.text()).toBe('')
    expect(region.isVisible()).toBe(true)

    data.enrichmentJob = makeRunningJob({
      running: false,
      completed: true,
      errors: ['tmdb: HTTP 401', 'tmdb: HTTP 500', 'tmdb: abandoned for this run'],
    })
    await flushPromises()

    // The same node, so the text changed under a region already being watched.
    expect(region.text()).toContain('3')
  })

  it('announces a second run reporting as many errors as the first', async () => {
    const wrapper = mountWithEnrichment({
      enrichmentJob: makeRunningJob({ completed: true, errors: ['tmdb: HTTP 500'] }),
    })
    const data = useDataStore()
    const region = wrapper.get('[data-testid="enrichment-errors-status"]')

    data.enrichmentJob = makeRunningJob()
    await flushPromises()
    // An announcement that never goes back to silent leaves the run below with
    // identical text, which no screen reader reads out.
    expect(region.text()).toBe('')

    data.enrichmentJob = makeRunningJob({
      running: false,
      completed: true,
      errors: ['rawg: HTTP 500'],
    })
    await flushPromises()

    expect(region.text()).toContain('1')
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

      await wrapper.findAll('[role="radio"]').find((p) => p.text() === 'Movie')!.trigger('click')
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
