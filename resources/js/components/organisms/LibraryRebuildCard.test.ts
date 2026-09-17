import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import LibraryRebuildCard from './LibraryRebuildCard.vue'
import { useDataStore } from '@/stores/data'
import type { LibraryRebuildResponse } from '@/types/api'

vi.mock('@/composables/useApi', () => ({
  useApi: () => ({
    get: vi.fn(),
    post: vi.fn(),
  }),
}))

enableAutoUnmount(afterEach)

function makeRunningJob(
  overrides: Partial<LibraryRebuildResponse> = {},
): LibraryRebuildResponse {
  return {
    running: true,
    completed: false,
    cancelled: false,
    total_items: 50,
    items_processed: 25,
    items_changed: 10,
    current_item: 'Processing item...',
    errors: [],
    ...overrides,
  }
}

describe('LibraryRebuildCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function mountCard(overrides: Partial<ReturnType<typeof useDataStore>> = {}) {
    const data = useDataStore()
    data.rebuildJob = null
    Object.assign(data, overrides)
    return mount(LibraryRebuildCard, { attachTo: document.body })
  }

  it('starts the pass and confirms it, so the click has an outcome', async () => {
    const wrapper = mountCard()
    const data = useDataStore()
    data.startRebuild = vi.fn().mockResolvedValue(makeRunningJob())

    await wrapper.find('[data-testid="library-rebuild-start"]').trigger('click')
    await flushPromises()

    expect(data.startRebuild).toHaveBeenCalled()
    expect(wrapper.get('[data-testid="library-rebuild-message"]').text()).toBe(
      'Library rebuild started.',
    )
  })

  it('says a refused start instead of leaving the button looking dead', async () => {
    const wrapper = mountCard()
    const data = useDataStore()
    data.startRebuild = vi
      .fn()
      .mockRejectedValue(new Error('A library rebuild is already running.'))

    await wrapper.find('[data-testid="library-rebuild-start"]').trigger('click')
    await flushPromises()

    const alert = wrapper.get('[data-testid="library-rebuild-error"]')
    expect(alert.text()).toContain('already running')
    expect(document.activeElement).toBe(alert.element)
  })

  it('offers Stop only while a rebuild is running, and stops it', async () => {
    expect(mountCard().find('[data-testid="library-rebuild-stop"]').exists()).toBe(false)

    const wrapper = mountCard({ rebuildJob: makeRunningJob() })
    const data = useDataStore()
    data.stopRebuild = vi.fn().mockResolvedValue('Library rebuild stop requested')

    await wrapper.find('[data-testid="library-rebuild-stop"]').trigger('click')
    await flushPromises()

    expect(data.stopRebuild).toHaveBeenCalled()
  })

  it('keeps the keyboard on the button it guards while the request runs', async () => {
    const wrapper = mountCard()
    const data = useDataStore()
    data.startRebuild = vi.fn().mockResolvedValue(makeRunningJob())

    const button = wrapper.get('[data-testid="library-rebuild-start"]')
    ;(button.element as HTMLElement).focus()
    await button.trigger('click')
    await flushPromises()

    expect(button.attributes('disabled')).toBeUndefined()
    expect(document.activeElement).toBe(button.element)
  })

  it('mounts the progress region before the first tick, not along with it', () => {
    const wrapper = mountCard()

    const region = wrapper.get('[data-testid="library-rebuild-progress-status"]')
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('')
    expect(wrapper.get('.rebuild-status').attributes('aria-live')).toBeUndefined()
  })

  it('says nothing across polls that cross no milestone, and speaks once when one does', async () => {
    const wrapper = mountCard({ rebuildJob: makeRunningJob({ items_processed: 15 }) })
    const data = useDataStore()
    const region = wrapper.get('[data-testid="library-rebuild-progress-status"]')
    const inside = region.text()

    for (const items_processed of [16, 17, 18, 19]) {
      data.rebuildJob = makeRunningJob({ items_processed })
      await nextTick()
      expect(region.text()).toBe(inside)
    }

    data.rebuildJob = makeRunningJob({ items_processed: 30 })
    await nextTick()

    expect(region.text()).not.toBe(inside)
  })

  it('announces the end of a run it saw, not a result stored by one it did not', async () => {
    const wrapper = mountCard({
      rebuildJob: makeRunningJob({ running: false, completed: true }),
    })
    const data = useDataStore()
    const region = wrapper.get('[data-testid="library-rebuild-progress-status"]')
    expect(region.text()).toBe('')

    data.rebuildJob = makeRunningJob()
    await nextTick()
    data.rebuildJob = makeRunningJob({
      running: false,
      completed: true,
      items_changed: 7,
    })
    await nextTick()

    expect(region.text()).toContain('finished')
    expect(region.text()).toContain('7')
  })

  it('shows the item being rebuilt and the counts while it runs', () => {
    const wrapper = mountCard({
      rebuildJob: makeRunningJob({
        items_processed: 30,
        total_items: 50,
        current_item: 'Dune',
      }),
    })

    expect(wrapper.text()).toContain('Dune')
    expect(wrapper.text()).toContain('30/50')
    expect(wrapper.text()).toContain('60%')
  })

  it('shows why a rebuild stopped instead of dropping the reason', () => {
    const wrapper = mountCard({
      rebuildJob: makeRunningJob({
        running: false,
        errors: ['the rebuild stopped on an error'],
      }),
    })

    expect(wrapper.get('[data-testid="library-rebuild-errors"]').text()).toContain(
      'stopped on an error',
    )
  })
})
