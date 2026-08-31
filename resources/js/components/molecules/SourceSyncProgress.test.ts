import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SourceSyncProgress from './SourceSyncProgress.vue'
import { progressMilestone } from '@/utils/format'
import type { SyncJobResponse } from '@/types/api'

const SOURCE = 'Steam'
const CROSSING = [...Array(101).keys()].find(percent => progressMilestone(percent) > 0)!

const running = (percent: number): SyncJobResponse =>
  ({
    source: SOURCE,
    status: 'running',
    items_processed: percent,
    current_item: `item ${percent}`,
    progress_percent: percent,
  }) as SyncJobResponse

describe('SourceSyncProgress', () => {
  it('speaks once a milestone is crossed, and says nothing on the polls between', async () => {
    const wrapper = mount(SourceSyncProgress, {
      props: { sourceName: SOURCE, job: running(CROSSING - 2) },
    })
    const region = wrapper.get('[data-testid="sync-progress-status"]')
    const opening = region.text()

    await wrapper.setProps({ job: running(CROSSING - 1) })

    expect(region.attributes('aria-live')).toBe('polite')
    expect(wrapper.get('.source-progress-counts').attributes('aria-live')).toBeUndefined()
    expect(region.text()).toBe(opening)

    await wrapper.setProps({ job: running(CROSSING) })

    expect(region.text()).not.toBe(opening)
  })
})
